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
        job.next_attempt_at = notification_jobs._now()
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
    assert (
        await claim_triage_work(
            mailbox_email="owner@example.com",
            message_id="gmail-message-1",
            source_history_id=101,
        )
        is None
    )
    async with notification_db() as session:
        work = await session.get(notification_jobs.GmailTriageWork, triage.id)
        assert work is not None
        assert work.next_attempt_at is not None
        work.next_attempt_at = notification_jobs._now()
        await session.commit()
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


@pytest.mark.asyncio
async def test_deleted_history_message_is_terminal_noop(notification_db, monkeypatch):
    owner_id = uuid.uuid4()
    await seed_mailbox_state(notification_db, owner_id)

    async def missing_message(_request, **_kwargs):
        raise mail_notifications.GoogleOperationRejected(404)

    monkeypatch.setattr(mail_notifications, "execute_google_request", missing_message)

    class Messages:
        def get(self, **_kwargs):
            return object()

    class Users:
        def messages(self):
            return Messages()

    class MissingMessageService:
        def users(self):
            return Users()

    completed = await mail_notifications._triage_message(
        service=MissingMessageService(),
        owner_id=owner_id,
        mailbox_email="owner@example.com",
        message_id="deleted-message",
        source_history_id=101,
    )
    assert completed is True

    async with notification_db() as session:
        work = await session.scalar(
            select(notification_jobs.GmailTriageWork).where(
                notification_jobs.GmailTriageWork.message_id == "deleted-message"
            )
        )
        assert work is not None
        assert work.state == "noop"
        assert work.last_error_code == "message_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_kind", "expected_error"),
    [
        ("credentials", "watch_credentials_unavailable"),
        ("provider", "watch_provider_unavailable"),
    ],
)
async def test_watch_renewal_failure_releases_claimed_lease(
    notification_db, monkeypatch, failure_kind, expected_error
):
    owner_id = uuid.uuid4()
    await seed_mailbox_state(notification_db, owner_id)

    async def configured(_owner_id):
        return "owner@example.com"

    monkeypatch.setattr(mail_notifications, "configured_automation_mailbox", configured)
    if failure_kind == "credentials":

        async def unavailable_service(_owner_id):
            raise mail_notifications.GoogleCredentialsUnavailable()

        monkeypatch.setattr(mail_notifications, "_gmail_service_for_owner", unavailable_service)
    else:

        class WatchUsers:
            def watch(self, **_kwargs):
                return object()

        class WatchService:
            def users(self):
                return WatchUsers()

        async def available_service(_owner_id):
            return WatchService()

        async def unavailable_watch(_request, **_kwargs):
            raise mail_notifications.GoogleProviderError()

        monkeypatch.setattr(mail_notifications, "_gmail_service_for_owner", available_service)
        monkeypatch.setattr(mail_notifications, "execute_google_request", unavailable_watch)

    assert await mail_notifications.renew_automation_watch(owner_id) is False
    async with notification_db() as session:
        state = await session.get(GmailMailboxState, "owner@example.com")
        assert state is not None
        assert state.watch_lease_token is None
        assert state.watch_lease_expires_at is None
        assert state.last_error_code == expected_error
        assert state.watch_last_error_code == expected_error


@pytest.mark.asyncio
async def test_attempt_limited_job_becomes_durable_resync_recovery(notification_db, monkeypatch):
    owner_id = uuid.uuid4()

    async def configured(_owner_id):
        return "owner@example.com"

    monkeypatch.setattr(notification_jobs, "configured_automation_mailbox", configured)
    await seed_mailbox_state(notification_db, owner_id)
    envelope = parse_notification_envelope(push_body(message_id="pubsub-attempt-limit"), "projects/test/subscriptions/onebox")
    await enqueue_notification(envelope, owner_id)
    claim = await claim_notification_job()
    assert claim is not None

    async with notification_db() as session:
        job = await session.get(GmailNotificationJob, claim.id)
        assert job is not None
        job.attempt_count = notification_jobs.settings.GMAIL_NOTIFICATION_MAX_ATTEMPTS
        await session.commit()

    await fail_job(claim, error_code="history_unavailable")
    async with notification_db() as session:
        job = await session.get(GmailNotificationJob, claim.id)
        state = await session.get(GmailMailboxState, "owner@example.com")
        assert job is not None
        assert job.state == "pending"
        assert job.lease_token is None
        assert state is not None
        assert state.resync_required is True
        assert state.last_error_code == "history_unavailable"

    recovery_claim = await claim_notification_job()
    assert recovery_claim is not None
    assert recovery_claim.id == claim.id


@pytest.mark.asyncio
async def test_stale_attempt_limited_lease_becomes_resync_recovery(notification_db, monkeypatch):
    owner_id = uuid.uuid4()

    async def configured(_owner_id):
        return "owner@example.com"

    monkeypatch.setattr(notification_jobs, "configured_automation_mailbox", configured)
    await seed_mailbox_state(notification_db, owner_id)
    envelope = parse_notification_envelope(push_body(message_id="pubsub-stale-attempt-limit"), "projects/test/subscriptions/onebox")
    await enqueue_notification(envelope, owner_id)
    claim = await claim_notification_job()
    assert claim is not None

    async with notification_db() as session:
        job = await session.get(GmailNotificationJob, claim.id)
        assert job is not None
        job.attempt_count = notification_jobs.settings.GMAIL_NOTIFICATION_MAX_ATTEMPTS
        job.lease_expires_at = notification_jobs._now()
        await session.commit()

    # A crashed worker first receives persisted retry backoff rather than an
    # immediate reclaim; once its due retry reaches the cap it starts resync.
    assert await claim_notification_job() is None
    async with notification_db() as session:
        job = await session.get(GmailNotificationJob, claim.id)
        state = await session.get(GmailMailboxState, "owner@example.com")
        assert job is not None
        assert job.state == "pending"
        assert job.last_error_code == "job_lease_expired"
        assert job.next_attempt_at is not None
        assert state is not None
        assert state.resync_state == "idle"
        job.next_attempt_at = notification_jobs._now()
        await session.commit()

    assert await claim_notification_job() is None
    async with notification_db() as session:
        job = await session.get(GmailNotificationJob, claim.id)
        state = await session.get(GmailMailboxState, "owner@example.com")
        assert job is not None
        assert job.last_error_code == "attempt_limit_exceeded"
        assert state is not None
        assert state.resync_required is True
        assert state.resync_state == "required"

    recovery_claim = await claim_notification_job()
    assert recovery_claim is not None
    assert recovery_claim.id == claim.id


@pytest.mark.asyncio
async def test_retryable_notification_failure_is_not_reclaimed_until_due(notification_db, monkeypatch):
    owner_id = uuid.uuid4()

    async def configured(_owner_id):
        return "owner@example.com"

    monkeypatch.setattr(notification_jobs, "configured_automation_mailbox", configured)
    await seed_mailbox_state(notification_db, owner_id)
    await enqueue_notification(
        parse_notification_envelope(push_body(message_id="pubsub-backoff"), "projects/test/subscriptions/onebox"),
        owner_id,
    )
    claim = await claim_notification_job()
    assert claim is not None
    await fail_job(claim, error_code="history_unavailable")

    async with notification_db() as session:
        job = await session.get(GmailNotificationJob, claim.id)
        assert job is not None
        assert job.state == "pending"
        assert job.next_attempt_at is not None
        assert job.next_attempt_at > notification_jobs._now()
        job.next_attempt_at = notification_jobs._now()
        await session.commit()

    retry = await claim_notification_job()
    assert retry is not None
    assert retry.id == claim.id
    assert retry.lease_token != claim.lease_token


@pytest.mark.asyncio
async def test_stale_resync_transition_cannot_restore_completed_generation(notification_db, monkeypatch):
    owner_id = uuid.uuid4()

    async def configured(_owner_id):
        return "owner@example.com"

    monkeypatch.setattr(notification_jobs, "configured_automation_mailbox", configured)
    await seed_mailbox_state(notification_db, owner_id)
    await enqueue_notification(
        parse_notification_envelope(push_body(message_id="pubsub-fenced-resync"), "projects/test/subscriptions/onebox"),
        owner_id,
    )
    job = await claim_notification_job()
    assert job is not None
    await fail_job(job, error_code="history_cursor_expired", require_resync=True)
    trigger = await claim_notification_job()
    assert trigger is not None
    resync = await notification_jobs.claim_mailbox_resync(trigger, owner_id)
    assert resync is not None

    assert await notification_jobs.complete_bounded_resync(resync, 150) is True
    assert await notification_jobs.checkpoint_bounded_resync(
        resync,
        next_page_token="stale-page",
        processed_count=1,
    ) is False
    assert await notification_jobs.release_mailbox_resync(resync, error_code="stale") is False

    async with notification_db() as session:
        state = await session.get(GmailMailboxState, "owner@example.com")
        assert state is not None
        assert state.resync_state == "idle"
        assert state.resync_required is False
        assert state.history_cursor == 150
        assert state.resync_page_token is None


@pytest.mark.asyncio
async def test_resync_total_limit_requires_manual_recovery_without_cursor_advance(notification_db, monkeypatch):
    owner_id = uuid.uuid4()

    async def configured(_owner_id):
        return "owner@example.com"

    class Messages:
        def list(self, **kwargs):
            observed["max_results"] = kwargs["maxResults"]
            return object()

    class Users:
        def messages(self):
            return Messages()

    class Service:
        def users(self):
            return Users()

    observed = {}
    monkeypatch.setattr(notification_jobs, "configured_automation_mailbox", configured)
    monkeypatch.setattr(notification_jobs.settings, "GMAIL_RESYNC_MAX_MESSAGES", 2)
    monkeypatch.setattr(mail_notifications.settings, "GMAIL_RESYNC_MAX_MESSAGES", 2)
    await seed_mailbox_state(notification_db, owner_id)
    await enqueue_notification(
        parse_notification_envelope(push_body(message_id="pubsub-resync-limit"), "projects/test/subscriptions/onebox"),
        owner_id,
    )
    job = await claim_notification_job()
    assert job is not None
    await fail_job(job, error_code="history_cursor_expired", require_resync=True)
    async with notification_db() as session:
        state = await session.get(GmailMailboxState, "owner@example.com")
        assert state is not None
        state.resync_message_count = 1
        await session.commit()
    trigger = await claim_notification_job()
    assert trigger is not None
    resync = await notification_jobs.claim_mailbox_resync(trigger, owner_id)
    assert resync is not None

    async def execute(_request, **_kwargs):
        return {"messages": [{"id": "m-1"}], "nextPageToken": "more"}

    async def terminal_triage(**_kwargs):
        return True

    monkeypatch.setattr(mail_notifications, "execute_google_request", execute)
    monkeypatch.setattr(mail_notifications, "_triage_message", terminal_triage)
    outcome = await mail_notifications._bounded_resync(
        service=Service(),
        owner_id=owner_id,
        mailbox_email="owner@example.com",
        source_history_id=100,
        resync=resync,
    )
    assert outcome == "manual_required"
    assert observed["max_results"] == 1

    async with notification_db() as session:
        state = await session.get(GmailMailboxState, "owner@example.com")
        assert state is not None
        assert state.resync_state == "manual_required"
        assert state.history_cursor == 100
        assert state.last_error_code == "resync_total_limit_reached"


@pytest.mark.asyncio
async def test_malformed_gmail_payload_is_terminal_without_llm(notification_db, monkeypatch):
    owner_id = uuid.uuid4()
    await seed_mailbox_state(notification_db, owner_id)

    class Messages:
        def get(self, **_kwargs):
            return object()

    class Users:
        def messages(self):
            return Messages()

    class Service:
        def users(self):
            return Users()

    async def malformed_message(_request, **_kwargs):
        return {}

    monkeypatch.setattr(mail_notifications, "execute_google_request", malformed_message)
    completed = await mail_notifications._triage_message(
        service=Service(),
        owner_id=owner_id,
        mailbox_email="owner@example.com",
        message_id="malformed-message",
        source_history_id=101,
    )
    assert completed is True

    async with notification_db() as session:
        work = await session.scalar(
            select(notification_jobs.GmailTriageWork).where(
                notification_jobs.GmailTriageWork.message_id == "malformed-message"
            )
        )
        assert work is not None
        assert work.state == "dead_letter"
        assert work.triage_summary is None
        assert work.last_error_code == "message_payload_invalid"


@pytest.mark.asyncio
async def test_stale_normal_history_work_is_fenced_by_resync_generation(notification_db, monkeypatch):
    owner_id = uuid.uuid4()

    async def configured(_owner_id):
        return "owner@example.com"

    monkeypatch.setattr(notification_jobs, "configured_automation_mailbox", configured)
    await seed_mailbox_state(notification_db, owner_id)
    await enqueue_notification(
        parse_notification_envelope(push_body(message_id="pubsub-stale-normal-failure"), "projects/test/subscriptions/onebox"),
        owner_id,
    )
    stale_failure = await claim_notification_job()
    assert stale_failure is not None
    assert stale_failure.resync_generation == 0

    # A newer recovery generation has already completed while this history
    # worker was still using its old cursor snapshot.
    async with notification_db() as session:
        state = await session.get(GmailMailboxState, "owner@example.com")
        assert state is not None
        state.resync_generation = 1
        state.resync_state = "idle"
        state.resync_required = False
        state.history_cursor = 150
        await session.commit()

    await fail_job(stale_failure, error_code="history_cursor_expired", require_resync=True)
    async with notification_db() as session:
        state = await session.get(GmailMailboxState, "owner@example.com")
        job = await session.get(GmailNotificationJob, stale_failure.id)
        assert state is not None and job is not None
        assert state.resync_generation == 1
        assert state.resync_state == "idle"
        assert state.history_cursor == 150
        assert job.state == "pending"
        assert job.last_error_code == "mailbox_recovery_changed"

    await enqueue_notification(
        parse_notification_envelope(push_body(message_id="pubsub-stale-normal-complete", history="151"), "projects/test/subscriptions/onebox"),
        owner_id,
    )
    stale_completion = await claim_notification_job()
    assert stale_completion is not None
    assert stale_completion.resync_generation == 1
    async with notification_db() as session:
        state = await session.get(GmailMailboxState, "owner@example.com")
        assert state is not None
        state.resync_generation = 2
        state.resync_state = "manual_required"
        state.resync_required = True
        await session.commit()

    assert await notification_jobs.complete_job_and_advance_cursor(stale_completion, history_cursor=999) is False
    async with notification_db() as session:
        state = await session.get(GmailMailboxState, "owner@example.com")
        job = await session.get(GmailNotificationJob, stale_completion.id)
        assert state is not None and job is not None
        assert state.history_cursor == 150
        assert state.resync_state == "manual_required"
        assert job.state == "pending"
        assert job.last_error_code == "mailbox_recovery_changed"
