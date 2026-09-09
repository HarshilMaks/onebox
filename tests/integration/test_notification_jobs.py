"""Priority 7 durable Gmail notification job coverage."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from server.models import Base, GmailMailboxState, GmailNotificationJob
from server.services import notification_jobs
from server.services.notification_jobs import (
    AutomationBaselineUnavailable,
    NotificationEnvelope,
    NotificationValidationError,
    claim_notification_job,
    claim_triage_work,
    enqueue_notification,
    fail_job,
    parse_notification_envelope,
    release_triage_work,
)
from server.workers import mail_notifications


@pytest.fixture
async def notification_db(monkeypatch):
    configured = make_url(os.environ["DATABASE_URL"])
    database_name = f"onebox_p7_{uuid.uuid4().hex}"
    admin_url = configured.set(drivername="postgresql+asyncpg", database="postgres")
    admin_dsn = admin_url.render_as_string(hide_password=False).replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    database_url = configured.set(database=database_name)
    try:
        admin = await asyncpg.connect(admin_dsn)
    except Exception as exc:
        pytest.skip(f"disposable PostgreSQL database is unavailable: {exc}")
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}"')
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(notification_jobs, "AsyncSessionLocal", sessions)
        yield sessions
        await engine.dispose()
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            database_name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        await admin.close()


def push_body(*, message_id: str = "pubsub-1", mailbox: str = "owner@example.com", history: str = "101") -> bytes:
    data = base64.b64encode(json.dumps({"emailAddress": mailbox, "historyId": history}).encode()).decode()
    return json.dumps(
        {
            "subscription": "projects/test/subscriptions/onebox",
            "message": {"messageId": message_id, "data": data},
        }
    ).encode()


async def seed_mailbox_state(session_factory, owner_id):
    async with session_factory() as session:
        session.add(
            GmailMailboxState(
                mailbox_email="owner@example.com",
                user_id=owner_id,
                history_cursor=100,
                watch_history_id=100,
            )
        )
        await session.commit()


def test_notification_envelope_rejects_wrong_subscription_and_invalid_history(monkeypatch):
    subscription = "projects/test/subscriptions/onebox"
    parsed = parse_notification_envelope(push_body(), subscription)
    assert parsed == NotificationEnvelope("pubsub-1", "owner@example.com", 101)

    with pytest.raises(NotificationValidationError):
        parse_notification_envelope(push_body(), "projects/other/subscriptions/onebox")
    with pytest.raises(NotificationValidationError):
        parse_notification_envelope(push_body(history="not-a-number"), subscription)
    with pytest.raises(NotificationValidationError):
        parse_notification_envelope(b"{}", subscription)


@pytest.mark.asyncio
async def test_enqueue_deduplicates_before_ack_and_two_workers_claim_once(notification_db, monkeypatch):
    owner_id = uuid.uuid4()

    async def configured(_owner_id):
        return "owner@example.com"

    monkeypatch.setattr(notification_jobs, "configured_automation_mailbox", configured)
    await seed_mailbox_state(notification_db, owner_id)
    envelope = parse_notification_envelope(push_body(), "projects/test/subscriptions/onebox")
    assert await enqueue_notification(envelope, owner_id) is True
    assert await enqueue_notification(envelope, owner_id) is False

    claims = await asyncio.gather(*[claim_notification_job() for _ in range(10)])
    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1
    assert claimed[0].history_id == 101

    async with notification_db() as session:
        jobs = (await session.scalars(select(GmailNotificationJob))).all()
    assert len(jobs) == 1
    assert jobs[0].attempt_count == 1
    assert jobs[0].state == "processing"


class _Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _History:
    def list(self, **kwargs):
        if kwargs.get("pageToken") is None:
            return _Request(
                {
                    "historyId": "102",
                    "history": [{"id": "102", "messagesAdded": [{"message": {"id": "m-1"}}]}],
                    "nextPageToken": "next",
                }
            )
        return _Request(
            {
                "historyId": "103",
                "history": [{"id": "103", "messagesAdded": [{"message": {"id": "m-2"}}]}],
            }
        )


class _Users:
    def history(self):
        return _History()


class FakeGmail:
    def users(self):
        return _Users()


@pytest.mark.asyncio
async def test_history_pagination_collects_all_message_work(monkeypatch):
    async def execute(request, **_kwargs):
        return request.execute()

    monkeypatch.setattr(mail_notifications, "execute_google_request", execute)
    messages, cursor = await mail_notifications._history_message_ids(FakeGmail(), 100)
    assert messages == ["m-1", "m-2"]
    assert cursor == 103


def test_durable_lifecycle_never_stops_mailbox_watch():
    source = open("server/main.py", encoding="utf-8").read()
    assert "users().stop" not in source
    assert "run_notification_worker" in source


@pytest.mark.asyncio
async def test_resync_and_retryable_triage_work_are_reclaimable(notification_db, monkeypatch):
    owner_id = uuid.uuid4()

    async def configured(_owner_id):
        return "owner@example.com"

    monkeypatch.setattr(notification_jobs, "configured_automation_mailbox", configured)
    await seed_mailbox_state(notification_db, owner_id)
    envelope = parse_notification_envelope(push_body(message_id="pubsub-resync"), "projects/test/subscriptions/onebox")
    await enqueue_notification(envelope, owner_id)
    claim = await claim_notification_job()
    assert claim is not None
    await fail_job(claim, error_code="history_cursor_expired", require_resync=True)
    reclaimed = await claim_notification_job()
    assert reclaimed is not None
    assert reclaimed.id == claim.id
    await fail_job(reclaimed, error_code="resync_checkpointed", require_resync=True)
    async with notification_db() as session:
        job = await session.get(GmailNotificationJob, claim.id)
        job.attempt_count = notification_jobs.settings.GMAIL_NOTIFICATION_MAX_ATTEMPTS
        await session.commit()
    beyond_cap = await claim_notification_job()
    assert beyond_cap is not None
    assert beyond_cap.id == claim.id

    async with notification_db() as session:
        mailbox_state = await session.get(GmailMailboxState, "owner@example.com")
        assert mailbox_state.resync_required is True

    triage = await claim_triage_work(
        mailbox_email="owner@example.com",
        message_id="gmail-message-1",
        source_history_id=101,
    )
    assert triage is not None
    await release_triage_work(triage, error_code="triage_provider_unavailable")
    retried = await claim_triage_work(
        mailbox_email="owner@example.com",
        message_id="gmail-message-1",
        source_history_id=101,
    )
    assert retried is not None
    assert retried.id == triage.id


@pytest.mark.asyncio
async def test_enqueue_refuses_unbaselined_mailbox(notification_db, monkeypatch):
    owner_id = uuid.uuid4()

    async def configured(_owner_id):
        return "owner@example.com"

    monkeypatch.setattr(notification_jobs, "configured_automation_mailbox", configured)
    envelope = parse_notification_envelope(push_body(message_id="pubsub-unbaselined"), "projects/test/subscriptions/onebox")
    with pytest.raises(AutomationBaselineUnavailable):
        await enqueue_notification(envelope, owner_id)
