"""Deterministic retention cleanup for terminal backend records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete

from server.config import settings
from server.database import AsyncSessionLocal
from server.models import GmailNotificationJob, GmailTriageWork, PendingAction


_RESOLVED_ACTION_STATUSES = ("succeeded", "failed", "rejected", "expired")
_TERMINAL_JOB_STATES = ("succeeded", "dead_letter")
_TERMINAL_TRIAGE_STATES = ("succeeded", "noop", "dead_letter")


@dataclass(frozen=True)
class RetentionCleanupResult:
    pending_actions: int
    notification_jobs: int
    triage_work: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def cleanup_retained_records(db: Any, *, now: datetime | None = None) -> RetentionCleanupResult:
    """Delete only terminal records older than configured retention cutoffs.

    Reconciliation-required actions intentionally remain untouched: they retain
    their payload/evidence until an authorized operator resolves the outcome.
    Pending-action audit rows cascade with their parent action.
    """
    current = now or _now()
    actions_cutoff = current - timedelta(days=settings.PENDING_ACTION_RETENTION_DAYS)
    jobs_cutoff = current - timedelta(days=settings.GMAIL_JOB_RETENTION_DAYS)
    triage_cutoff = current - timedelta(days=settings.GMAIL_TRIAGE_RETENTION_DAYS)

    actions = await db.execute(
        delete(PendingAction).where(
            PendingAction.status.in_(_RESOLVED_ACTION_STATUSES),
            PendingAction.processed_at.is_not(None),
            PendingAction.processed_at < actions_cutoff,
        )
    )
    jobs = await db.execute(
        delete(GmailNotificationJob).where(
            GmailNotificationJob.state.in_(_TERMINAL_JOB_STATES),
            GmailNotificationJob.processed_at.is_not(None),
            GmailNotificationJob.processed_at < jobs_cutoff,
        )
    )
    triage = await db.execute(
        delete(GmailTriageWork).where(
            GmailTriageWork.state.in_(_TERMINAL_TRIAGE_STATES),
            GmailTriageWork.processed_at.is_not(None),
            GmailTriageWork.processed_at < triage_cutoff,
        )
    )
    return RetentionCleanupResult(
        pending_actions=actions.rowcount or 0,
        notification_jobs=jobs.rowcount or 0,
        triage_work=triage.rowcount or 0,
    )


async def prune_retained_records(*, now: datetime | None = None) -> RetentionCleanupResult:
    """Run one transactional cleanup pass for periodic worker scheduling."""
    async with AsyncSessionLocal() as db:
        result = await cleanup_retained_records(db, now=now)
        await db.commit()
        return result
