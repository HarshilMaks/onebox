"""Durable Gmail Pub/Sub job persistence and state transitions.

HTTP ingress only validates and commits a job.  Workers claim finite leases and
perform Gmail history/triage later, so a successful Pub/Sub acknowledgement can
never be the only copy of a notification.
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from server.config import settings
from server.database import AsyncSessionLocal
from server.models import GmailMailboxState, GmailNotificationJob, GmailTriageWork
from server.services.credentials import load_connected_google_connection


class NotificationValidationError(ValueError):
    """The push request is malformed or does not target the configured mailbox."""


class AutomationBaselineUnavailable(RuntimeError):
    """A valid notification cannot yet be safely queued against a mailbox cursor."""


@dataclass(frozen=True, slots=True)
class NotificationEnvelope:
    pubsub_message_id: str
    mailbox_email: str
    history_id: int


@dataclass(frozen=True, slots=True)
class ClaimedNotificationJob:
    id: UUID
    mailbox_email: str
    history_id: int
    lease_token: str


@dataclass(frozen=True, slots=True)
class ClaimedTriageWork:
    id: UUID
    lease_token: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 320:
        raise NotificationValidationError("mailbox email is invalid")
    value = value.strip()
    _display, address = parseaddr(value)
    if address != value or address.count("@") != 1:
        raise NotificationValidationError("mailbox email is invalid")
    return address.casefold()


def _bounded_identifier(value: object, name: str, maximum: int = 255) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise NotificationValidationError(f"{name} is invalid")
    value = value.strip()
    if any(character.isspace() or ord(character) < 33 for character in value):
        raise NotificationValidationError(f"{name} is invalid")
    return value


def _history_id(value: object) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise NotificationValidationError("history ID is invalid")
    parsed = int(value)
    if parsed <= 0 or parsed > 9_223_372_036_854_775_807:
        raise NotificationValidationError("history ID is invalid")
    return parsed


def parse_notification_envelope(raw_body: bytes, expected_subscription: str | None) -> NotificationEnvelope:
    """Decode one bounded Pub/Sub envelope without trusting its nested data."""
    if not raw_body or len(raw_body) > settings.PUBSUB_MAX_ENVELOPE_BYTES:
        raise NotificationValidationError("notification body is invalid")
    try:
        envelope = json.loads(raw_body)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotificationValidationError("notification body is invalid") from exc
    if not isinstance(envelope, dict) or envelope.get("subscription") != expected_subscription:
        raise NotificationValidationError("notification subscription is invalid")
    message = envelope.get("message")
    if not isinstance(message, dict):
        raise NotificationValidationError("notification message is invalid")
    message_id = _bounded_identifier(message.get("messageId"), "notification message ID")
    encoded = message.get("data")
    if not isinstance(encoded, str) or len(encoded) > settings.PUBSUB_MAX_ENVELOPE_BYTES * 2:
        raise NotificationValidationError("notification data is invalid")
    try:
        decoded = base64.b64decode(encoded, validate=True)
        notification = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotificationValidationError("notification data is invalid") from exc
    if not isinstance(notification, dict):
        raise NotificationValidationError("notification data is invalid")
    return NotificationEnvelope(
        pubsub_message_id=message_id,
        mailbox_email=_normalize_email(notification.get("emailAddress")),
        history_id=_history_id(notification.get("historyId")),
    )


async def configured_automation_mailbox(user_id: UUID) -> str:
    """Read the persisted normalized Google mailbox without a provider round trip."""
    async with AsyncSessionLocal() as db:
        connection = await load_connected_google_connection(user_id, db)
        return _normalize_email(connection.google_email)


async def enqueue_notification(envelope: NotificationEnvelope, owner_id: UUID) -> bool:
    """Durably deduplicate one push delivery before the route returns 2xx."""
    expected_mailbox = await configured_automation_mailbox(owner_id)
    if envelope.mailbox_email != expected_mailbox:
        raise NotificationValidationError("notification mailbox is not configured")

    async with AsyncSessionLocal() as db:
        state = await db.get(GmailMailboxState, envelope.mailbox_email)
        if state is None or state.history_cursor is None:
            raise AutomationBaselineUnavailable("mailbox history baseline is not ready")
        if state.user_id != owner_id:
            raise NotificationValidationError("notification mailbox owner is invalid")

        job = GmailNotificationJob(
            pubsub_message_id=envelope.pubsub_message_id,
            mailbox_email=envelope.mailbox_email,
            history_id=envelope.history_id,
            state="pending",
        )
        db.add(job)
        try:
            await db.commit()
            return True
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(GmailNotificationJob.id).where(
                    GmailNotificationJob.pubsub_message_id == envelope.pubsub_message_id
                )
            )
            if existing is not None:
                return False
            raise


async def claim_notification_job() -> ClaimedNotificationJob | None:
    """Claim one pending/stale job with a finite lease using SKIP LOCKED."""
    now = _now()
    lease_duration = timedelta(seconds=settings.GMAIL_NOTIFICATION_LEASE_SECONDS)
    async with AsyncSessionLocal() as db:
        jobs = (
            await db.scalars(
                select(GmailNotificationJob)
                .where(
                    (GmailNotificationJob.state == "pending")
                    | (
                        (GmailNotificationJob.state == "processing")
                        & (GmailNotificationJob.lease_expires_at <= now)
                    )
                )
                .order_by(GmailNotificationJob.received_at, GmailNotificationJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
        ).all()
        if not jobs:
            return None
        job = jobs[0]
        mailbox_state = await db.get(GmailMailboxState, job.mailbox_email)
        resync_active = bool(mailbox_state and mailbox_state.resync_required)
        if job.attempt_count >= settings.GMAIL_NOTIFICATION_MAX_ATTEMPTS and not resync_active:
            job.state = "dead_letter"
            job.last_error_code = "attempt_limit_exceeded"
            job.processed_at = now
            await db.commit()
            return None
        job.state = "processing"
        job.attempt_count += 1
        job.lease_token = secrets.token_urlsafe(32)
        job.lease_expires_at = now + lease_duration
        await db.commit()
        return ClaimedNotificationJob(
            id=job.id,
            mailbox_email=job.mailbox_email,
            history_id=job.history_id,
            lease_token=job.lease_token,
        )


async def job_mailbox_state(job: ClaimedNotificationJob) -> GmailMailboxState | None:
    async with AsyncSessionLocal() as db:
        return await db.get(GmailMailboxState, job.mailbox_email)


async def claim_triage_work(
    *,
    mailbox_email: str,
    message_id: str,
    source_history_id: int,
) -> ClaimedTriageWork | None:
    """Deduplicate message work, allowing only expired analysis leases to be reclaimed."""
    now = _now()
    lease_duration = timedelta(seconds=settings.GMAIL_NOTIFICATION_LEASE_SECONDS)
    async with AsyncSessionLocal() as db:
        row = await db.scalar(
            select(GmailTriageWork)
            .where(GmailTriageWork.mailbox_email == mailbox_email, GmailTriageWork.message_id == message_id)
            .with_for_update()
        )
        if row is None:
            row = GmailTriageWork(
                mailbox_email=mailbox_email,
                message_id=message_id,
                source_history_id=source_history_id,
                state="processing",
                attempt_count=1,
                lease_token=secrets.token_urlsafe(32),
                lease_expires_at=now + lease_duration,
            )
            db.add(row)
            try:
                await db.commit()
                return ClaimedTriageWork(id=row.id, lease_token=row.lease_token)
            except IntegrityError:
                # Another history worker inserted the same message while this
                # transaction was open. Lock its row and apply normal lease
                # rules instead of treating uniqueness as a worker crash.
                await db.rollback()
                row = await db.scalar(
                    select(GmailTriageWork)
                    .where(
                        GmailTriageWork.mailbox_email == mailbox_email,
                        GmailTriageWork.message_id == message_id,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise
        if row.state in {"succeeded", "noop", "dead_letter"}:
            return None
        if row.lease_expires_at is not None and row.lease_expires_at > now:
            return None
        row.attempt_count += 1
        row.lease_token = secrets.token_urlsafe(32)
        row.lease_expires_at = now + lease_duration
        await db.commit()
        return ClaimedTriageWork(id=row.id, lease_token=row.lease_token)


async def finalize_triage_work(
    claim: ClaimedTriageWork,
    *,
    state: str,
    summary: str | None = None,
    error_code: str | None = None,
) -> None:
    if state not in {"succeeded", "noop", "dead_letter"}:
        raise ValueError("invalid triage terminal state")
    async with AsyncSessionLocal() as db:
        row = await db.scalar(
            select(GmailTriageWork)
            .where(
                GmailTriageWork.id == claim.id,
                GmailTriageWork.state == "processing",
                GmailTriageWork.lease_token == claim.lease_token,
            )
            .with_for_update()
        )
        if row is None:
            return
        row.state = state
        row.triage_summary = summary[:4_000] if summary else None
        row.last_error_code = error_code
        row.processed_at = _now()
        await db.commit()


async def triage_work_is_terminal(*, mailbox_email: str, message_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        state = await db.scalar(
            select(GmailTriageWork.state).where(
                GmailTriageWork.mailbox_email == mailbox_email,
                GmailTriageWork.message_id == message_id,
            )
        )
        return state in {"succeeded", "noop", "dead_letter"}


async def complete_job_and_advance_cursor(
    claim: ClaimedNotificationJob,
    *,
    history_cursor: int,
) -> None:
    """Advance the mailbox cursor only after all history work is durably terminal."""
    async with AsyncSessionLocal() as db:
        job = await db.scalar(
            select(GmailNotificationJob)
            .where(
                GmailNotificationJob.id == claim.id,
                GmailNotificationJob.state == "processing",
                GmailNotificationJob.lease_token == claim.lease_token,
            )
            .with_for_update()
        )
        if job is None:
            return
        state = await db.scalar(
            select(GmailMailboxState)
            .where(GmailMailboxState.mailbox_email == claim.mailbox_email)
            .with_for_update()
        )
        if state is None:
            job.state = "dead_letter"
            job.last_error_code = "mailbox_state_missing"
        else:
            state.history_cursor = max(state.history_cursor or 0, history_cursor)
            state.last_error_code = None
            job.state = "succeeded"
            job.last_error_code = None
        job.processed_at = _now()
        await db.commit()


async def fail_job(
    claim: ClaimedNotificationJob,
    *,
    error_code: str,
    dead_letter: bool = False,
    require_resync: bool = False,
) -> None:
    """Persist a safe worker outcome; never advance a cursor after failure."""
    async with AsyncSessionLocal() as db:
        job = await db.scalar(
            select(GmailNotificationJob)
            .where(
                GmailNotificationJob.id == claim.id,
                GmailNotificationJob.state == "processing",
                GmailNotificationJob.lease_token == claim.lease_token,
            )
            .with_for_update()
        )
        if job is None:
            return
        if require_resync:
            state = await db.scalar(
                select(GmailMailboxState)
                .where(GmailMailboxState.mailbox_email == claim.mailbox_email)
                .with_for_update()
            )
            if state is not None:
                state.resync_required = True
                state.last_error_code = error_code
        job.last_error_code = error_code
        if dead_letter or (
            job.attempt_count >= settings.GMAIL_NOTIFICATION_MAX_ATTEMPTS and not require_resync
        ):
            job.state = "dead_letter"
            job.processed_at = _now()
        else:
            job.state = "pending"
            job.lease_token = None
            job.lease_expires_at = None
        await db.commit()


async def claim_watch_renewal(mailbox_email: str, owner_id: UUID) -> str | None:
    """Take the singleton mailbox watch lease if renewal is due."""
    now = _now()
    async with AsyncSessionLocal() as db:
        state = await db.scalar(
            select(GmailMailboxState)
            .where(GmailMailboxState.mailbox_email == mailbox_email)
            .with_for_update()
        )
        if state is None:
            state = GmailMailboxState(mailbox_email=mailbox_email, user_id=owner_id)
            db.add(state)
        elif state.user_id != owner_id:
            return None
        due_at = now + timedelta(seconds=settings.GMAIL_WATCH_RENEWAL_SECONDS)
        if state.watch_expires_at is not None and state.watch_expires_at > due_at:
            return None
        if state.watch_lease_expires_at is not None and state.watch_lease_expires_at > now:
            return None
        state.watch_lease_token = secrets.token_urlsafe(32)
        state.watch_lease_expires_at = now + timedelta(seconds=settings.GMAIL_NOTIFICATION_LEASE_SECONDS)
        await db.commit()
        return state.watch_lease_token


async def complete_watch_renewal(
    mailbox_email: str,
    lease_token: str,
    *,
    history_id: int,
    expires_at: datetime,
) -> bool:
    async with AsyncSessionLocal() as db:
        state = await db.scalar(
            select(GmailMailboxState)
            .where(
                GmailMailboxState.mailbox_email == mailbox_email,
                GmailMailboxState.watch_lease_token == lease_token,
            )
            .with_for_update()
        )
        if state is None:
            return False
        state.watch_history_id = history_id
        state.history_cursor = state.history_cursor or history_id
        state.watch_expires_at = expires_at
        state.watch_lease_token = None
        state.watch_lease_expires_at = None
        state.last_error_code = None
        await db.commit()
        return True


async def fail_watch_renewal(mailbox_email: str, lease_token: str, error_code: str) -> None:
    async with AsyncSessionLocal() as db:
        state = await db.scalar(
            select(GmailMailboxState)
            .where(
                GmailMailboxState.mailbox_email == mailbox_email,
                GmailMailboxState.watch_lease_token == lease_token,
            )
            .with_for_update()
        )
        if state is None:
            return
        state.last_error_code = error_code
        state.watch_lease_token = None
        state.watch_lease_expires_at = None
        await db.commit()


async def automation_status(owner_id: UUID) -> dict[str, Any] | None:
    """Return safe operator observability fields, never message content."""
    async with AsyncSessionLocal() as db:
        state = await db.scalar(select(GmailMailboxState).where(GmailMailboxState.user_id == owner_id))
        if state is None:
            return None
        # Count without exposing job contents.
        from sqlalchemy import func

        queue_depth = await db.scalar(
            select(func.count()).select_from(GmailNotificationJob).where(
                GmailNotificationJob.mailbox_email == state.mailbox_email,
                GmailNotificationJob.state.in_(("pending", "processing")),
            )
        )
        failure_count = await db.scalar(
            select(func.count()).select_from(GmailNotificationJob).where(
                GmailNotificationJob.mailbox_email == state.mailbox_email,
                GmailNotificationJob.state == "dead_letter",
            )
        )
        return {
            "mailbox": state.mailbox_email,
            "history_cursor": state.history_cursor,
            "watch_expires_at": state.watch_expires_at,
            "resync_required": state.resync_required,
            "resync_message_count": state.resync_message_count,
            "last_error_code": state.last_error_code,
            "watch_lease_expires_at": state.watch_lease_expires_at,
            "queue_depth": queue_depth or 0,
            "failure_count": failure_count or 0,
        }


async def complete_bounded_resync(mailbox_email: str, owner_id: UUID, history_cursor: int) -> bool:
    async with AsyncSessionLocal() as db:
        state = await db.scalar(
            select(GmailMailboxState)
            .where(
                GmailMailboxState.mailbox_email == mailbox_email,
                GmailMailboxState.user_id == owner_id,
                GmailMailboxState.resync_required.is_(True),
            )
            .with_for_update()
        )
        if state is None:
            return False
        state.history_cursor = history_cursor
        state.resync_required = False
        state.resync_page_token = None
        state.resync_message_count = 0
        state.last_error_code = None
        await db.commit()
        return True


async def release_triage_work(claim: ClaimedTriageWork, *, error_code: str) -> None:
    """Release a retryable triage lease without erasing its durable error/attempts."""
    async with AsyncSessionLocal() as db:
        row = await db.scalar(
            select(GmailTriageWork)
            .where(
                GmailTriageWork.id == claim.id,
                GmailTriageWork.state == "processing",
                GmailTriageWork.lease_token == claim.lease_token,
            )
            .with_for_update()
        )
        if row is None:
            return
        row.lease_token = None
        row.lease_expires_at = None
        row.last_error_code = error_code
        await db.commit()


async def checkpoint_bounded_resync(
    mailbox_email: str,
    owner_id: UUID,
    *,
    next_page_token: str | None,
    processed_count: int,
) -> bool:
    async with AsyncSessionLocal() as db:
        state = await db.scalar(
            select(GmailMailboxState)
            .where(
                GmailMailboxState.mailbox_email == mailbox_email,
                GmailMailboxState.user_id == owner_id,
                GmailMailboxState.resync_required.is_(True),
            )
            .with_for_update()
        )
        if state is None:
            return False
        state.resync_page_token = next_page_token
        state.resync_message_count += processed_count
        state.last_error_code = "history_cursor_expired"
        await db.commit()
        return True
