"""Durable, owner-authorized execution of agent-requested side effects."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from server.database import AsyncSessionLocal
from server.integrations.google import (
    GoogleOperationRejected,
    GoogleOperationTimeout,
    GoogleProviderError,
    execute_google_request,
    run_google_operation,
)
from server.models import PendingAction
from tools.idempotency import make_idempotency_key, make_payload_hash

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


class PendingActionNotFound(Exception):
    pass


class PendingActionInvalidState(Exception):
    pass


class PendingActionExecutionError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def action_to_dict(action: PendingAction) -> Dict[str, Any]:
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
    }


async def create_pending_action(
    user_id: str,
    action_type: str,
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    """Persist an immutable action request and return an existing duplicate if present."""
    if action_type not in SUPPORTED_ACTIONS:
        raise ValueError(f"Unsupported pending action type: {action_type}")

    owner_id = UUID(str(user_id))
    immutable_payload = dict(payload)
    idempotency_key = make_idempotency_key(str(owner_id), action_type, immutable_payload)

    async with AsyncSessionLocal() as db:
        existing = await db.scalar(
            select(PendingAction).where(PendingAction.idempotency_key == idempotency_key)
        )
        if existing:
            return action_to_dict(existing)

        action = PendingAction(
            user_id=owner_id,
            action_type=action_type,
            payload=immutable_payload,
            payload_hash=make_payload_hash(immutable_payload),
            idempotency_key=idempotency_key,
            summary=_summary(action_type, immutable_payload),
            status="pending",
            expires_at=_now() + PENDING_ACTION_TTL,
        )
        db.add(action)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(PendingAction).where(PendingAction.idempotency_key == idempotency_key)
            )
            if existing:
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


async def resolve_reply_target(
    gmail_service: Any,
    recipient_email: str,
    subject_filter: str,
) -> str:
    """Resolve the mutable search expression before approval and persist its message ID."""
    request = gmail_service.users().messages().list(
        userId="me",
        q=_query_for_reply(recipient_email, subject_filter),
        maxResults=1,
    )
    response = await execute_google_request(request, resource=gmail_service)
    messages = response.get("messages", [])
    if not messages:
        raise PendingActionExecutionError("No matching email was found for this reply.")
    return messages[0]["id"]


async def get_pending_action(action_id: UUID, user_id: UUID) -> Dict[str, Any]:
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


async def reject_pending_action(action_id: UUID, user_id: UUID) -> Dict[str, Any]:
    async with AsyncSessionLocal() as db:
        action = await db.scalar(
            select(PendingAction)
            .where(PendingAction.id == action_id, PendingAction.user_id == user_id)
            .with_for_update()
        )
        if not action:
            raise PendingActionNotFound()
        if action.status == "pending":
            action.status = "rejected"
            action.processed_at = _now()
            await db.commit()
        elif action.status not in {"rejected", "expired"}:
            raise PendingActionInvalidState(f"Cannot reject action in {action.status} state")
        return action_to_dict(action)


async def claim_pending_action(action_id: UUID, user_id: UUID) -> Tuple[Dict[str, Any], bool]:
    """Atomically make a pending action processing so only one approval executes it."""
    async with AsyncSessionLocal() as db:
        action = await db.scalar(
            select(PendingAction)
            .where(PendingAction.id == action_id, PendingAction.user_id == user_id)
            .with_for_update()
        )
        if not action:
            raise PendingActionNotFound()

        if action.status == "pending" and action.expires_at <= _now():
            action.status = "expired"
            action.processed_at = _now()
            await db.commit()
            return action_to_dict(action), False

        if action.status != "pending":
            return action_to_dict(action), False

        action.status = "processing"
        action.approved_at = _now()
        await db.commit()
        return action_to_dict(action), True


def _execute_action_sync(
    action: Mapping[str, Any],
    gmail_service: Any,
    calendar_service: Any,
    tasks_service: Any,
) -> Dict[str, Any]:
    payload = action["payload"]
    action_type = action["action_type"]

    if action_type == ACTION_SEND_EMAIL:
        from tools.email.send_gmail import send_new_email

        sent = send_new_email(
            gmail_service=gmail_service,
            sender_email=payload["sender_email"],
            to=[payload["recipient_email"]],
            subject=payload["subject"],
            body=payload["email_body"],
            raise_on_error=True,
        )
        if not sent or not sent.get("id"):
            raise PendingActionExecutionError("Email provider did not confirm the send.")
        return {"external_id": sent["id"]}

    if action_type == ACTION_SEND_REPLY:
        from tools.email.send_gmail import send_reply_email

        sent = send_reply_email(
            gmail_service=gmail_service,
            user_email=payload["sender_email"],
            original_email_id=payload["original_message_id"],
            reply_body=payload["reply_message"],
            reply_to_all=False,
            raise_on_error=True,
        )
        if not sent or not sent.get("id"):
            raise PendingActionExecutionError("Email provider did not confirm the reply.")
        return {"external_id": sent["id"], "original_message_id": payload["original_message_id"]}

    if action_type == ACTION_CREATE_EVENT:
        raise PendingActionExecutionError("Calendar actions use the async provider adapter.")

    if action_type == ACTION_CREATE_TASK:
        from tools.tasks.tasks_tool import get_or_create_task_list, insert_task

        task_list_id = get_or_create_task_list(
            tasks_service,
            "Executive Agent Tasks",
            raise_on_error=True,
        )
        if not task_list_id:
            raise PendingActionExecutionError("Task provider did not provide a task list.")
        task = insert_task(
            tasks_service,
            task_list_id,
            {"title": payload["title"], "notes": payload["notes"]},
            raise_on_error=True,
        )
        if not task or not task.get("id"):
            raise PendingActionExecutionError("Task provider did not confirm task creation.")
        return {"external_id": task["id"]}

    raise PendingActionExecutionError("Unsupported pending action type.")


async def _execute_calendar_action(
    action: Mapping[str, Any],
    calendar_service: Any,
) -> Dict[str, Any]:
    """Execute the complete calendar write through the bounded request adapter."""
    payload = action["payload"]
    request_id = str(action["id"]).replace("-", "")
    attendees = payload.get("attendee_emails", [])
    request = calendar_service.events().insert(
        calendarId="primary",
        body={
            "summary": payload["title"],
            "location": payload.get("location", ""),
            "description": payload.get("description", ""),
            "start": {"dateTime": payload["start_time_iso"], "timeZone": payload["event_timezone"]},
            "end": {"dateTime": payload["end_time_iso"], "timeZone": payload["event_timezone"]},
            "attendees": [{"email": email} for email in attendees],
            "reminders": {"useDefault": True},
            "conferenceData": {
                "createRequest": {
                    "requestId": request_id,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        },
        conferenceDataVersion=1,
        sendUpdates="all" if attendees else "none",
    )
    event = await execute_google_request(request, resource=calendar_service)
    if not event or not event.get("id"):
        raise PendingActionExecutionError("Calendar provider did not confirm event creation.")
    return {"external_id": event["id"]}


async def execute_claimed_action(
    action: Mapping[str, Any],
    gmail_service: Any,
    calendar_service: Any,
    tasks_service: Any,
) -> Dict[str, Any]:
    """Execute a claimed action once and durably record its final outcome.

    A processing action is never automatically retried after a process crash or
    ambiguous provider response, because retrying could duplicate an external
    side effect. It must instead be reconciled by an operator.
    """
    try:
        if action["action_type"] == ACTION_CREATE_EVENT:
            result = await _execute_calendar_action(action, calendar_service)
        else:
            resource = gmail_service or calendar_service or tasks_service
            result = await run_google_operation(
                _execute_action_sync,
                action,
                gmail_service,
                calendar_service,
                tasks_service,
                resource=resource,
                passthrough=(PendingActionExecutionError,),
            )
        status = "succeeded"
        error_code: Optional[str] = None
        processed_at: datetime | None = _now()
    except GoogleOperationTimeout:
        logger.warning("Pending action %s timed out", action["id"])
        result = None
        status = "failed"
        error_code = "provider_timeout"
        processed_at = _now()
    except GoogleOperationRejected:
        logger.warning("Pending action %s was rejected by the provider", action["id"])
        result = None
        status = "failed"
        error_code = "provider_rejected"
        processed_at = _now()
    except GoogleProviderError:
        logger.warning("Pending action %s provider operation failed", action["id"])
        result = None
        status = "failed"
        error_code = "provider_unavailable"
        processed_at = _now()
    except PendingActionExecutionError as exc:
        logger.warning("Pending action %s failed: %s", action["id"], exc)
        result = None
        status = "failed"
        error_code = "provider_not_confirmed"
        processed_at = _now()
    except Exception:
        logger.exception("Pending action %s failed unexpectedly", action["id"])
        result = None
        status = "failed"
        error_code = "execution_failed"
        processed_at = _now()

    async with AsyncSessionLocal() as db:
        row = await db.get(PendingAction, action["id"])
        if not row:
            raise PendingActionNotFound()
        row.status = status
        row.result = result
        row.error_code = error_code
        row.processed_at = processed_at
        await db.commit()
        await db.refresh(row)
        return action_to_dict(row)
