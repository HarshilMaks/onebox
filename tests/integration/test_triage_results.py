"""Owner-scoped triage result listing against disposable PostgreSQL."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from server.models import AgentToken, GmailTriageWork
from server.pagination import InvalidPageToken
from server.services.triage_results import PREVIEW_CHARS, TriageResultNotFound, get_triage_result, list_triage_results

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _connect_user(sessions, google_email: str | None) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with sessions() as db:
        db.add(AgentToken(user_id=user_id, google_email_normalized=google_email, connection_status="connected"))
        await db.commit()
    return user_id


async def _add_result(
    sessions,
    mailbox: str,
    *,
    state: str = "succeeded",
    summary: str | None = None,
    minutes: int = 0,
    error: str | None = None,
) -> uuid.UUID:
    created = BASE + timedelta(minutes=minutes)
    async with sessions() as db:
        row = GmailTriageWork(
            mailbox_email=mailbox,
            message_id=f"msg-{uuid.uuid4().hex[:10]}",
            source_history_id=1,
            state=state,
            triage_summary=summary,
            last_error_code=error,
            created_at=created,
            # A message that is still being processed has no completion time yet.
            processed_at=None if state == "processing" else created,
        )
        db.add(row)
        await db.commit()
        return row.id


async def _ids(page) -> list[uuid.UUID]:
    return [item["id"] for item in page["results"]]


@pytest.mark.asyncio
async def test_a_user_can_only_read_their_own_mailbox_results(pending_db):
    alice = await _connect_user(pending_db, "alice@example.com")
    bob = await _connect_user(pending_db, "bob@example.com")
    stranger = uuid.uuid4()  # signed in but never connected a Google account
    no_email = await _connect_user(pending_db, None)  # token row without a Google identity yet
    alice_rows = [await _add_result(pending_db, "alice@example.com", summary="alice mail", minutes=m) for m in (1, 2)]
    bob_row = await _add_result(pending_db, "bob@example.com", summary="bob mail")

    assert set(await _ids(await list_triage_results(alice, state="succeeded", limit=20))) == set(alice_rows)
    assert await _ids(await list_triage_results(bob, state="succeeded", limit=20)) == [bob_row]
    assert (await list_triage_results(stranger, state="succeeded", limit=20))["results"] == []
    assert (await list_triage_results(no_email, state="succeeded", limit=20))["results"] == []

    with pytest.raises(TriageResultNotFound):
        await get_triage_result(alice_rows[0], bob)  # someone else's result looks like it doesn't exist
    with pytest.raises(TriageResultNotFound):
        await get_triage_result(alice_rows[0], stranger)
    assert (await get_triage_result(alice_rows[0], alice))["summary"] == "alice mail"


@pytest.mark.asyncio
async def test_listing_returns_one_requested_state_and_exposes_the_error_code_of_failures(pending_db):
    user = await _connect_user(pending_db, "carol@example.com")
    ok = await _add_result(pending_db, "carol@example.com", state="succeeded", summary="summary")
    skipped = await _add_result(pending_db, "carol@example.com", state="noop")
    failed = await _add_result(pending_db, "carol@example.com", state="dead_letter", error="message_payload_invalid")
    working = await _add_result(pending_db, "carol@example.com", state="processing")

    assert await _ids(await list_triage_results(user, state="succeeded", limit=20)) == [ok]
    assert await _ids(await list_triage_results(user, state="noop", limit=20)) == [skipped]
    assert await _ids(await list_triage_results(user, state="processing", limit=20)) == [working]
    dead = (await list_triage_results(user, state="dead_letter", limit=20))["results"]
    assert [(item["id"], item["last_error_code"]) for item in dead] == [(failed, "message_payload_invalid")]


@pytest.mark.asyncio
async def test_list_shows_a_short_preview_and_only_the_detail_has_the_full_summary(pending_db):
    user = await _connect_user(pending_db, "dave@example.com")
    long_summary = "x" * 1000
    with_summary = await _add_result(pending_db, "dave@example.com", summary=long_summary)
    without_summary = await _add_result(pending_db, "dave@example.com", state="noop", minutes=1)

    items = {item["id"]: item for item in (await list_triage_results(user, state="succeeded", limit=20))["results"]}
    assert items[with_summary]["summary_preview"] == long_summary[:PREVIEW_CHARS]
    assert "summary" not in items[with_summary]
    noop_item = (await list_triage_results(user, state="noop", limit=20))["results"][0]
    assert noop_item["id"] == without_summary and noop_item["summary_preview"] is None

    assert (await get_triage_result(with_summary, user))["summary"] == long_summary


@pytest.mark.asyncio
async def test_keyset_pagination_works_for_rows_that_have_no_completion_time_yet(pending_db):
    user = await _connect_user(pending_db, "erin@example.com")
    rows = []
    for index, minutes in enumerate([0, 1, 2, 2, 3]):  # two rows share a timestamp
        row_id = await _add_result(pending_db, "erin@example.com", state="processing", minutes=minutes)
        rows.append((BASE + timedelta(minutes=minutes), row_id))
    expected = [row_id for _, row_id in sorted(rows, reverse=True)]

    seen, sizes, token = [], [], None
    while True:
        page = await list_triage_results(user, state="processing", limit=2, page_token=token)
        seen += await _ids(page)
        sizes.append(len(page["results"]))
        token = page["next_page_token"]
        if token is None:
            break

    assert seen == expected and sizes == [2, 2, 1]


@pytest.mark.asyncio
async def test_another_users_page_token_cannot_widen_the_owner_filter(pending_db):
    alice = await _connect_user(pending_db, "alice2@example.com")
    bob = await _connect_user(pending_db, "bob2@example.com")
    bob_row = await _add_result(pending_db, "bob2@example.com", minutes=0)
    for minutes in (1, 2, 3):
        await _add_result(pending_db, "alice2@example.com", minutes=minutes)
    alice_token = (await list_triage_results(alice, state="succeeded", limit=1))["next_page_token"]

    page = await list_triage_results(bob, state="succeeded", limit=20, page_token=alice_token)

    assert await _ids(page) == [bob_row]  # Bob's own older row only; none of Alice's


@pytest.mark.asyncio
async def test_a_malformed_page_token_is_rejected_before_any_query(pending_db):
    user = await _connect_user(pending_db, "frank@example.com")

    with pytest.raises(InvalidPageToken):
        await list_triage_results(user, state="succeeded", limit=20, page_token="not-a-token")
