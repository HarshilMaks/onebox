"""Owner-scoped, read-only access to the background worker's triage results."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select, tuple_

from server.database import AsyncSessionLocal
from server.models import AgentToken, GmailTriageWork
from server.pagination import decode_page_token, encode_page_token

PREVIEW_CHARS = 280


class TriageResultNotFound(Exception):
    pass


def _owner_mailbox(user_id: UUID):
    """The caller's verified Google account, case-folded exactly as triage rows store it.

    Ownership never comes from the request. A user with no connected account (no token
    row, or no Google email yet) matches nothing, because comparing with NULL is never true.
    """
    return select(AgentToken.google_email_normalized).where(AgentToken.user_id == user_id).scalar_subquery()


async def list_triage_results(
    user_id: UUID,
    *,
    state: str,
    limit: int,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List the owner's triage results in one state, newest first, with summary previews.

    Pages by (created_at, id) like the actions list. `created_at` is always set, whereas
    `processed_at` is empty while a message is still processing, so it cannot be a cursor.
    """
    conditions = [GmailTriageWork.mailbox_email == _owner_mailbox(user_id), GmailTriageWork.state == state]
    if page_token is not None:
        cursor_created_at, cursor_id = decode_page_token(page_token)
        conditions.append(
            tuple_(GmailTriageWork.created_at, GmailTriageWork.id) < tuple_(cursor_created_at, cursor_id)
        )

    statement = (
        select(
            GmailTriageWork.id,
            GmailTriageWork.message_id,
            GmailTriageWork.state,
            # Cut in the database so a page never loads up to 4,000 characters per row.
            func.left(GmailTriageWork.triage_summary, PREVIEW_CHARS).label("summary_preview"),
            GmailTriageWork.last_error_code,
            GmailTriageWork.created_at,
            GmailTriageWork.processed_at,
        )
        .where(*conditions)
        .order_by(GmailTriageWork.created_at.desc(), GmailTriageWork.id.desc())
        .limit(limit + 1)  # one extra row tells us whether another page exists
    )
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(statement)).all()

    page = rows[:limit]
    return {
        "results": [dict(row._mapping) for row in page],
        "next_page_token": (
            encode_page_token(page[-1].created_at, page[-1].id) if len(rows) > limit else None
        ),
    }


async def get_triage_result(result_id: UUID, user_id: UUID) -> dict[str, Any]:
    """Return one of the owner's results with the full summary; anyone else's looks like 404."""
    statement = select(
        GmailTriageWork.id,
        GmailTriageWork.message_id,
        GmailTriageWork.state,
        GmailTriageWork.triage_summary.label("summary"),
        GmailTriageWork.last_error_code,
        GmailTriageWork.created_at,
        GmailTriageWork.processed_at,
    ).where(
        GmailTriageWork.id == result_id,
        GmailTriageWork.mailbox_email == _owner_mailbox(user_id),
    )
    async with AsyncSessionLocal() as db:
        row = (await db.execute(statement)).one_or_none()
    if row is None:
        raise TriageResultNotFound()
    return dict(row._mapping)
