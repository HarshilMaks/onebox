"""Durable, owner-authorized execution of agent-requested side effects."""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from server.action_payloads import canonicalize_action_payload
from server.database import AsyncSessionLocal
from server.models import PendingAction, PendingActionAuditEvent
from server.services.action_handlers import (
    AmbiguousActionOutcome,
    ConfirmedActionFailure,
    ReconciliationResult,
    execute_action,
    reconcile_action,
)
from tools.idempotency import make_command_record_key, make_payload_hash

logger = logging.getLogger(__name__)

ACTION_SEND_EMAIL = "send_email"
ACTION_SEND_REPLY = "send_reply"
ACTION_CREATE_EVENT = "create_event"
ACTION_CREATE_TASK = "create_task"
SUPPORTED_ACTIONS = {
    ACTION_SEND_EMAIL,
    ACTION_SEND_REPLY,
    ACTION_CREATE_EVENT,
    ACTION_CREATE_TASK,
}
PENDING_ACTION_TTL = timedelta(minutes=15)
PROCESSING_LEASE = timedelta(minutes=2)
_COMMAND_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class PendingActionNotFound(Exception):
    pass


class PendingActionInvalidState(Exception):
    pass


class PendingActionCommandConflict(Exception):
    pass


class PendingActionExecutionError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def issue_command_key() -> str:
    """Issue a server-only opaque key for one agent/tool invocation."""
    return secrets.token_urlsafe(24)


def _validate_command_key(command_key: str) -> str:
    if not isinstance(command_key, str) or not _COMMAND_KEY_RE.fullmatch(command_key):
        raise ValueError("Invalid server command key")
    return command_key


def _command_record_key(user_id: UUID, command_key: str) -> str:
    """Compatibility value for the old column; it is not an intent hash."""
    return make_command_record_key(str(user_id), command_key)


def _summary(action_type: str, payload: Mapping[str, Any]) -> str:
    if action_type == ACTION_SEND_EMAIL:
        return f"Send email to {payload['recipient_email']} with subject '{payload['subject']}'"
    if action_type == ACTION_SEND_REPLY:
        return f"Reply to {payload['recipient_email']} in message {payload['original_message_id']}"
    if action_type == ACTION_CREATE_EVENT:
        attendees = payload.get("attendee_emails", [])
        suffix = f" with attendees {', '.join(attendees)}" if attendees else ""
        return f"Create event '{payload['title']}' from {payload['start_time_iso']} to {payload['end_time_iso']}{suffix}"
    if action_type == ACTION_CREATE_TASK:
        return f"Create task '{payload['title']}'"
    raise ValueError(f"Unsupported pending action type: {action_type}")


def action_to_dict(action: PendingAction) -> dict[str, Any]:
    """Serialize a DB action for route responses and internal dispatch.

    Attempt tokens are intentionally present only in this internal dictionary;
    `PendingActionResponse` does not expose them to approval clients.
    """
    return {
        "id": action.id,
        "action_type": action.action_type,
        "payload": action.payload,
        "payload_hash": action.payload_hash,
        "summary": action.summary,
        "status": action.status,
        "result": action.result,
        "error_code": action.error_code,
        "created_at": action.created_at,
        "expires_at": action.expires_at,
        "approved_at": action.approved_at,
        "processed_at": action.processed_at,
        "command_key": action.command_key,
        "attempt_token": action.attempt_token,
        "attempt_count": action.attempt_count,
        "attempt_started_at": action.attempt_started_at,
        "lease_expires_at": action.lease_expires_at,
        "reconciliation_reason": action.reconciliation_reason,
        "reconciliation_required_at": action.reconciliation_required_at,
    }


def _audit(
    db: Any,
    action: PendingAction,
    *,
    event_type: str,
    old_status: str | None,
    new_status: str,
    actor_user_id: UUID | None = None,
    reason: str | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> None:
    db.add(
        PendingActionAuditEvent(
            action_id=action.id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            old_status=old_status,
            new_status=new_status,
            attempt_token=action.attempt_token,
            reason=reason,
            evidence=dict(evidence) if evidence else None,
        )
    )


def _require_canonical_payload(action: PendingAction) -> dict[str, Any]:
    payload = canonicalize_action_payload(action.action_type, action.payload)
    if make_payload_hash(payload) != action.payload_hash:
        raise ValueError("payload hash mismatch")
    return payload


def _require_attempt_token(action: Mapping[str, Any]) -> str:
    token = action.get("attempt_token")
    if not isinstance(token, str) or not token:
        raise PendingActionInvalidState("Action claim has no attempt token")
    return token


def _mark_reconciliation_required(
    db: Any,
    action: PendingAction,
    *,
    reason: str,
    evidence: Mapping[str, Any] | None = None,
    actor_user_id: UUID | None = None,
    event_type: str = "reconciliation_required",
) -> None:
    old_status = action.status
    action.status = "reconciliation_required"
    action.error_code = reason
    action.reconciliation_reason = reason
    action.reconciliation_evidence = dict(evidence) if evidence else None
    action.reconciliation_required_at = _now()
    action.processed_at = _now()
    _audit(
        db,
        action,
        event_type=event_type,
        old_status=old_status,
        new_status="reconciliation_required",
        actor_user_id=actor_user_id,
        reason=reason,
        evidence=evidence,
    )


async def create_pending_action(
    user_id: str,
    action_type: str,
    payload: Mapping[str, Any],
    *,
    command_key: str,
) -> dict[str, Any]:
    """Persist one immutable command, deduplicated only within its command key.

    The calling agent issues and retains `command_key` before tool invocation;
    it is never supplied by the LLM and never minted at persistence time. A new
    key intentionally permits a later identical side effect.
    """
    if action_type not in SUPPORTED_ACTIONS:
        raise ValueError(f"Unsupported pending action type: {action_type}")
    owner_id = UUID(str(user_id))
    command_key = _validate_command_key(command_key)
    canonical_payload = canonicalize_action_payload(action_type, payload)
    payload_hash = make_payload_hash(canonical_payload)

    async with AsyncSessionLocal() as db:
        existing = await db.scalar(
            select(PendingAction).where(
                PendingAction.user_id == owner_id,
                PendingAction.command_key == command_key,
            )
        )
        if existing:
            if existing.action_type != action_type or existing.payload_hash != payload_hash:
                raise PendingActionCommandConflict("Command key was already used for another payload")
            return action_to_dict(existing)

        action = PendingAction(
            user_id=owner_id,
            action_type=action_type,
            payload=canonical_payload,
            payload_hash=payload_hash,
            idempotency_key=_command_record_key(owner_id, command_key),
            command_key=command_key,
            summary=_summary(action_type, canonical_payload),
            status="pending",
            expires_at=_now() + PENDING_ACTION_TTL,
        )
        db.add(action)
        try:
            await db.commit()
        except IntegrityError:
            # The unique owner/key index arbitrates concurrent duplicate tool
            # delivery. It must never turn a changed payload into a success.
            await db.rollback()
            existing = await db.scalar(
                select(PendingAction).where(
                    PendingAction.user_id == owner_id,
                    PendingAction.command_key == command_key,
                )
            )
            if existing:
                if existing.action_type != action_type or existing.payload_hash != payload_hash:
                    raise PendingActionCommandConflict("Command key was already used for another payload")
                return action_to_dict(existing)
            raise
        await db.refresh(action)
        return action_to_dict(action)


def _query_for_reply(recipient_email: str, subject_filter: str) -> str:
    query = f"from:({recipient_email})"
    if subject_filter:
        escaped_subject = subject_filter.replace('"', "")
        query += f' subject:"{escaped_subject}"'
    return query


def _header_addresses(value: str | None) -> list[str]:
    return [address.casefold() for _name, address in getaddresses([value or ""]) if address]


async def resolve_reply_target(
    gmail_service: Any,
    recipient_email: str,
    subject_filter: str,
) -> dict[str, str]:
    """Freeze all mutable reply routing/thread metadata before approval."""
    request = gmail_service.users().messages().list(
        userId="me",
        q=_query_for_reply(recipient_email, subject_filter),
        maxResults=1,
    )
    from server.integrations.google import execute_google_read_request
    from tools.utils import get_header_value

    response = await execute_google_read_request(request, resource=gmail_service)
    messages = response.get("messages", [])
    if not messages:
        raise PendingActionExecutionError("No matching email was found for this reply.")
    original_message_id = messages[0].get("id")
    if not original_message_id:
        raise PendingActionExecutionError("Reply target did not include a message ID.")
    source = await execute_google_read_request(
        gmail_service.users().messages().get(userId="me", id=original_message_id, format="metadata"),
        resource=gmail_service,
    )
    headers = source.get("payload", {}).get("headers", [])
    reply_to = get_header_value(headers, "Reply-To")
    sender = get_header_value(headers, "From")
    candidates = _header_addresses(reply_to) or _header_addresses(sender)
    thread_id = source.get("threadId")
    rfc_message_id = get_header_value(headers, "Message-ID")
    subject = get_header_value(headers, "Subject") or "(no subject)"
    if len(candidates) != 1 or not thread_id or not rfc_message_id:
        raise PendingActionExecutionError("Reply target did not include stable recipient/thread metadata.")
    # Pydantic performs final provider-ID/address validation when this is staged.
    return {
        "recipient_email": candidates[0],
        "original_message_id": original_message_id,
        "thread_id": thread_id,
        "original_rfc_message_id": rfc_message_id,
        "original_references": get_header_value(headers, "References") or "",
        "original_subject": subject,
    }


async def get_pending_action(action_id: UUID, user_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        action = await db.scalar(
            select(PendingAction).where(
                PendingAction.id == action_id,
                PendingAction.user_id == user_id,
            )
        )
        if not action:
            raise PendingActionNotFound()
        return action_to_dict(action)


async def get_pending_action_for_reconciliation(action_id: UUID) -> dict[str, Any]:
    """Fetch an action after route-level operator authorization, not by owner."""
    async with AsyncSessionLocal() as db:
        action = await db.get(PendingAction, action_id)
        if not action:
            raise PendingActionNotFound()
        return action_to_dict(action) | {"user_id": action.user_id}


async def reject_pending_action(action_id: UUID, user_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        action = await db.scalar(
            select(PendingAction)
            .where(PendingAction.id == action_id, PendingAction.user_id == user_id)
            .with_for_update()
        )
        if not action:
            raise PendingActionNotFound()
        if action.status == "pending":
            old_status = action.status
            action.status = "rejected"
            action.processed_at = _now()
            _audit(
                db,
                action,
                event_type="rejected",
                old_status=old_status,
                new_status="rejected",
                actor_user_id=user_id,
            )
            await db.commit()
        elif action.status not in {"rejected", "expired"}:
            raise PendingActionInvalidState(f"Cannot reject action in {action.status} state")
        return action_to_dict(action)


async def claim_pending_action(action_id: UUID, user_id: UUID) -> tuple[dict[str, Any], bool]:
    """Owner-lock, verify, and lease one action before provider dispatch."""
    async with AsyncSessionLocal() as db:
        action = await db.scalar(
            select(PendingAction)
            .where(PendingAction.id == action_id, PendingAction.user_id == user_id)
            .with_for_update()
        )
        if not action:
            raise PendingActionNotFound()
        now = _now()

        if action.status == "processing" and (
            action.lease_expires_at is None or action.lease_expires_at <= now
        ):
            _mark_reconciliation_required(
                db,
                action,
                reason="processing_lease_expired",
                evidence={"lease_expires_at": action.lease_expires_at.isoformat() if action.lease_expires_at else None},
            )
            await db.commit()
            return action_to_dict(action), False

        if action.status == "pending" and action.expires_at <= now:
            old_status = action.status
            action.status = "expired"
            action.processed_at = now
            _audit(db, action, event_type="expired", old_status=old_status, new_status="expired")
            await db.commit()
            return action_to_dict(action), False

        if action.status != "pending":
            return action_to_dict(action), False

        try:
            _require_canonical_payload(action)
        except (TypeError, ValueError):
            _mark_reconciliation_required(
                db,
                action,
                reason="payload_integrity_mismatch",
                evidence={"payload_hash": action.payload_hash},
            )
            await db.commit()
            return action_to_dict(action), False

        old_status = action.status
        action.status = "processing"
        action.approved_at = now
        action.attempt_token = secrets.token_urlsafe(32)
        action.attempt_count += 1
        action.attempt_started_at = now
        action.lease_expires_at = now + PROCESSING_LEASE
        _audit(
            db,
            action,
            event_type="claimed",
            old_status=old_status,
            new_status="processing",
            actor_user_id=user_id,
            evidence={"attempt_count": action.attempt_count},
        )
        await db.commit()  # Commit before any provider side effect.
        return action_to_dict(action), True


async def _finalize_claimed_action(
    action: Mapping[str, Any],
    *,
    status: str,
    result: Mapping[str, Any] | None,
    error_code: str | None,
    event_type: str,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fence finalization on status+attempt token so stale workers cannot win."""
    attempt_token = _require_attempt_token(action)
    action_id = UUID(str(action["id"]))
    async with AsyncSessionLocal() as db:
        row = await db.scalar(
            select(PendingAction)
            .where(
                PendingAction.id == action_id,
                PendingAction.status == "processing",
                PendingAction.attempt_token == attempt_token,
            )
            .with_for_update()
        )
        if row is None:
            current = await db.get(PendingAction, action_id)
            if current is None:
                raise PendingActionNotFound()
            return action_to_dict(current)

        if row.lease_expires_at is None or row.lease_expires_at <= _now():
            _mark_reconciliation_required(
                db,
                row,
                reason="processing_lease_expired",
                evidence={"finalizer": event_type},
            )
        elif status == "reconciliation_required":
            _mark_reconciliation_required(
                db,
                row,
                reason=error_code or "provider_outcome_unknown",
                evidence=evidence,
                event_type=event_type,
            )
        else:
            old_status = row.status
            row.status = status
            row.result = dict(result) if result else None
            row.error_code = error_code
            row.processed_at = _now()
            _audit(
                db,
                row,
                event_type=event_type,
                old_status=old_status,
                new_status=status,
                reason=error_code,
                evidence=evidence,
            )
        await db.commit()
        await db.refresh(row)
        return action_to_dict(row)


async def finalize_pre_dispatch_failure(action: Mapping[str, Any], error_code: str) -> dict[str, Any]:
    """End a claimed action only when provider dispatch definitely never began."""
    return await _finalize_claimed_action(
        action,
        status="failed",
        result=None,
        error_code=error_code,
        event_type="pre_dispatch_failed",
        evidence={"dispatch": "not_started"},
    )


async def execute_claimed_action(
    action: Mapping[str, Any],
    *,
    gmail_service: Any = None,
    calendar_service: Any = None,
    tasks_service: Any = None,
) -> dict[str, Any]:
    """Execute a leased command once; transport uncertainty requires reconciliation."""
    try:
        result = await execute_action(
            action,
            gmail_service=gmail_service,
            calendar_service=calendar_service,
            tasks_service=tasks_service,
        )
    except ConfirmedActionFailure as exc:
        logger.warning("Pending action %s failed before acceptance: %s", action["id"], exc.code)
        return await _finalize_claimed_action(
            action,
            status="failed",
            result=None,
            error_code=exc.code,
            event_type="confirmed_failure",
            evidence={"dispatch": "not_accepted"},
        )
    except AmbiguousActionOutcome as exc:
        logger.warning("Pending action %s needs reconciliation: %s", action["id"], exc.code)
        return await _finalize_claimed_action(
            action,
            status="reconciliation_required",
            result=None,
            error_code=exc.code,
            event_type="provider_outcome_ambiguous",
            evidence={"dispatch": "acceptance_unknown"},
        )
    except Exception:
        logger.exception("Pending action %s ended unexpectedly after claim", action["id"])
        return await _finalize_claimed_action(
            action,
            status="reconciliation_required",
            result=None,
            error_code="execution_interrupted",
            event_type="execution_interrupted",
            evidence={"dispatch": "acceptance_unknown"},
        )

    return await _finalize_claimed_action(
        action,
        status="succeeded",
        result=result,
        error_code=None,
        event_type="succeeded",
        evidence={"dispatch": "confirmed"},
    )


async def reconcile_pending_action(
    action_id: UUID,
    operator_user_id: UUID,
    *,
    gmail_service: Any = None,
    calendar_service: Any = None,
    tasks_service: Any = None,
) -> dict[str, Any]:
    """Perform a no-write reconciliation and audit the operator transition."""
    # Promote an expired lease under the action row lock. A worker may have
    # dispatched before crashing, so no owner approval/retry is required before
    # an authorized operator can investigate it.
    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(PendingAction).where(PendingAction.id == action_id).with_for_update())
        if row is None:
            raise PendingActionNotFound()
        if row.status == "processing" and (
            row.lease_expires_at is None or row.lease_expires_at <= _now()
        ):
            _mark_reconciliation_required(
                db,
                row,
                reason="processing_lease_expired",
                evidence={"promotion": "operator_reconciliation"},
                actor_user_id=operator_user_id,
                event_type="operator_promoted_expired_lease",
            )
            await db.commit()
        elif row.status != "reconciliation_required":
            raise PendingActionInvalidState("Only ambiguous actions can be reconciled")
        action = action_to_dict(row)

    outcome: ReconciliationResult = await reconcile_action(
        action,
        gmail_service=gmail_service,
        calendar_service=calendar_service,
        tasks_service=tasks_service,
    )

    async with AsyncSessionLocal() as db:
        row = await db.scalar(
            select(PendingAction)
            .where(PendingAction.id == action_id, PendingAction.status == "reconciliation_required")
            .with_for_update()
        )
        if row is None:
            current = await db.get(PendingAction, action_id)
            if current is None:
                raise PendingActionNotFound()
            return action_to_dict(current)

        old_status = row.status
        if outcome.status == "succeeded":
            row.status = "succeeded"
            row.result = outcome.result
            row.error_code = None
            row.processed_at = _now()
        elif outcome.status == "failed":
            row.status = "failed"
            row.result = None
            row.error_code = outcome.error_code
            row.processed_at = _now()
        else:
            # The state remains explicit; refresh only evidence/reason so the
            # next authorized reconciliation has an auditable trail.
            row.error_code = outcome.error_code
            row.reconciliation_reason = outcome.error_code
            row.reconciliation_evidence = outcome.evidence
            row.reconciliation_required_at = _now()

        _audit(
            db,
            row,
            event_type="operator_reconciled",
            old_status=old_status,
            new_status=row.status,
            actor_user_id=operator_user_id,
            reason=outcome.error_code,
            evidence=outcome.evidence,
        )
        await db.commit()
        await db.refresh(row)
        return action_to_dict(row)
