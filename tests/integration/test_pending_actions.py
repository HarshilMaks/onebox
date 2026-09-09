"""Priority 6 pending-action integration coverage against disposable PostgreSQL."""
from __future__ import annotations

import asyncio
import base64
import os
import uuid
from datetime import timedelta
from email import message_from_bytes
from typing import Any

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from server.integrations.google import GoogleOperationTimeout
from server.models import Base, PendingAction
from server.services import action_handlers, pending_actions
from server.services.action_handlers import calendar_event_id, gmail_message_id, task_marker
from server.services.pending_actions import (
    ACTION_SEND_EMAIL,
    PendingActionCommandConflict,
    claim_pending_action,
    create_pending_action,
    execute_claimed_action,
    finalize_pre_dispatch_failure,
    get_pending_action,
    reject_pending_action,
)


@pytest.fixture
async def pending_db(monkeypatch):
    """Give each test an isolated physical Postgres database and session factory."""
    configured = make_url(os.environ["DATABASE_URL"])
    database_name = f"onebox_p6_{uuid.uuid4().hex}"
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
    except Exception as exc:
        await admin.close()
        pytest.skip(f"disposable PostgreSQL database is unavailable: {exc}")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(pending_actions, "AsyncSessionLocal", sessions)
        yield sessions
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await admin.close()


@pytest.fixture
def owner_id():
    return uuid.uuid4()


def email_payload(subject: str = "Hello") -> dict[str, str]:
    return {
        "sender_email": "owner@example.com",
        "recipient_email": "recipient@example.com",
        "subject": subject,
        "email_body": "A durable message",
    }


class _Request:
    def __init__(self, callback):
        self._callback = callback

    def execute(self):
        return self._callback()


class _Messages:
    def __init__(self):
        self.send_calls: list[dict[str, Any]] = []

    def send(self, **kwargs):
        self.send_calls.append(kwargs)
        return _Request(lambda: {"id": f"message-{len(self.send_calls)}"})


class _Users:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages




class _ReconcileMessages:
    def __init__(self, command_key: str, recipient: str, *, match_count: int):
        self.command_key = command_key
        self.recipient = recipient
        self.match_count = match_count

    def list(self, **_kwargs):
        return _Request(lambda: {"messages": [{"id": f"found-{index}"} for index in range(self.match_count)]})

    def get(self, **_kwargs):
        return _Request(
            lambda: {
                "id": "found-0",
                "payload": {
                    "headers": [
                        {"name": "X-OneBox-Command-Key", "value": self.command_key},
                        {"name": "Message-ID", "value": gmail_message_id(self.command_key)},
                        {"name": "To", "value": self.recipient},
                    ]
                },
            }
        )


class FakeReconcileGmail:
    def __init__(self, command_key: str, recipient: str, *, match_count: int):
        self.messages_api = _ReconcileMessages(command_key, recipient, match_count=match_count)

    def users(self):
        return _Users(self.messages_api)


class _PaginatedReconcileMessages:
    def list(self, **kwargs):
        if kwargs.get("pageToken") is None:
            return _Request(lambda: {"messages": [{"id": "first"}], "nextPageToken": "next"})
        return _Request(lambda: {"messages": [{"id": "second"}]})

    def get(self, **_kwargs):
        raise AssertionError("a second marker match must stop before metadata lookup")


class FakePaginatedReconcileGmail:
    def users(self):
        return _Users(_PaginatedReconcileMessages())


class FakeGmail:
    def __init__(self):
        self.messages_api = _Messages()

    def users(self):
        return _Users(self.messages_api)


@pytest.fixture
def immediate_google(monkeypatch):
    async def execute(request, **_kwargs):
        return request.execute()

    monkeypatch.setattr(action_handlers, "execute_google_request", execute)


@pytest.mark.asyncio
async def test_command_key_idempotency_allows_later_identical_actions(pending_db, owner_id):
    key = "command-key-0001"
    first = await create_pending_action(str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key=key)
    duplicate = await create_pending_action(str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key=key)
    later = await create_pending_action(
        str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key="command-key-0002"
    )

    assert duplicate["id"] == first["id"]
    assert later["id"] != first["id"]
    with pytest.raises(PendingActionCommandConflict):
        await create_pending_action(
            str(owner_id), ACTION_SEND_EMAIL, email_payload("Changed"), command_key=key
        )


@pytest.mark.asyncio
async def test_owner_scope_and_concurrent_approvals_dispatch_once(pending_db, owner_id, immediate_google):
    action = await create_pending_action(
        str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key="command-key-concurrent"
    )
    other_owner = uuid.uuid4()
    with pytest.raises(pending_actions.PendingActionNotFound):
        await get_pending_action(action["id"], other_owner)
    with pytest.raises(pending_actions.PendingActionNotFound):
        await reject_pending_action(action["id"], other_owner)

    gmail = FakeGmail()

    async def approve_once():
        claimed_action, claimed = await claim_pending_action(action["id"], owner_id)
        if claimed:
            return await execute_claimed_action(claimed_action, gmail_service=gmail)
        return claimed_action

    results = await asyncio.gather(*[approve_once() for _ in range(20)])
    assert len(gmail.messages_api.send_calls) == 1
    assert sum(result["status"] == "succeeded" for result in results) >= 1
    saved = await get_pending_action(action["id"], owner_id)
    assert saved["status"] == "succeeded"
    assert saved["attempt_count"] == 1


@pytest.mark.asyncio
async def test_expiry_tampering_and_stale_finalizer_require_reconciliation(pending_db, owner_id):
    expired = await create_pending_action(
        str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key="command-key-expired"
    )
    async with pending_db() as session:
        row = await session.get(PendingAction, expired["id"])
        row.expires_at = pending_actions._now() - timedelta(seconds=1)
        await session.commit()
    expired_result, claimed = await claim_pending_action(expired["id"], owner_id)
    assert claimed is False
    assert expired_result["status"] == "expired"

    tampered = await create_pending_action(
        str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key="command-key-tampered"
    )
    async with pending_db() as session:
        row = await session.get(PendingAction, tampered["id"])
        row.payload = {**row.payload, "email_body": "database tampering"}
        await session.commit()
    corrupt_result, claimed = await claim_pending_action(tampered["id"], owner_id)
    assert claimed is False
    assert corrupt_result["status"] == "reconciliation_required"

    stale = await create_pending_action(
        str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key="command-key-stale"
    )
    claimed_action, claimed = await claim_pending_action(stale["id"], owner_id)
    assert claimed is True
    async with pending_db() as session:
        row = await session.get(PendingAction, stale["id"])
        row.status = "reconciliation_required"
        row.error_code = "operator_marked_unknown"
        await session.commit()
    final = await finalize_pre_dispatch_failure(claimed_action, "credentials_unavailable_before_dispatch")
    assert final["status"] == "reconciliation_required"
    assert final["error_code"] == "operator_marked_unknown"


@pytest.mark.asyncio
async def test_ambiguous_timeout_is_not_failed_or_retried(pending_db, owner_id, monkeypatch):
    action = await create_pending_action(
        str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key="command-key-timeout"
    )
    claimed_action, claimed = await claim_pending_action(action["id"], owner_id)
    assert claimed is True

    async def timeout(_request, **_kwargs):
        raise GoogleOperationTimeout()

    monkeypatch.setattr(action_handlers, "execute_google_request", timeout)
    result = await execute_claimed_action(claimed_action, gmail_service=FakeGmail())
    assert result["status"] == "reconciliation_required"
    assert result["error_code"] == "provider_outcome_unknown"


@pytest.mark.asyncio
async def test_gmail_markers_are_deterministic_and_auditable(pending_db, owner_id, immediate_google):
    command_key = "command-key-gmail-marker"
    action = await create_pending_action(str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key=command_key)
    claimed_action, claimed = await claim_pending_action(action["id"], owner_id)
    assert claimed is True
    gmail = FakeGmail()
    result = await execute_claimed_action(claimed_action, gmail_service=gmail)

    raw = gmail.messages_api.send_calls[0]["body"]["raw"]
    message = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert result["status"] == "succeeded"
    assert "".join(message["Message-ID"].split()) == gmail_message_id(command_key)
    assert message["X-OneBox-Command-Key"] == command_key
    assert calendar_event_id(command_key).startswith("ob")
    assert task_marker(command_key) in f"notes {task_marker(command_key)}"


@pytest.mark.asyncio
async def test_gmail_reconciliation_transitions_are_conservative(pending_db, owner_id, immediate_google):
    async def ambiguous_action(key: str):
        action = await create_pending_action(str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key=key)
        claimed, did_claim = await claim_pending_action(action["id"], owner_id)
        assert did_claim is True
        async with pending_db() as session:
            row = await session.get(PendingAction, action["id"])
            row.status = "reconciliation_required"
            row.error_code = "provider_outcome_unknown"
            await session.commit()
        return action, claimed

    succeeded, _ = await ambiguous_action("command-key-reconcile-success")
    success = await pending_actions.reconcile_pending_action(
        succeeded["id"],
        uuid.uuid4(),
        gmail_service=FakeReconcileGmail(succeeded["command_key"], "recipient@example.com", match_count=1),
    )
    assert success["status"] == "succeeded"

    missing, _ = await ambiguous_action("command-key-reconcile-missing")
    not_found = await pending_actions.reconcile_pending_action(
        missing["id"],
        uuid.uuid4(),
        gmail_service=FakeReconcileGmail(missing["command_key"], "recipient@example.com", match_count=0),
    )
    assert not_found["status"] == "failed"
    assert not_found["error_code"] == "provider_not_found"

    conflict, _ = await ambiguous_action("command-key-reconcile-conflict")
    ambiguous = await pending_actions.reconcile_pending_action(
        conflict["id"],
        uuid.uuid4(),
        gmail_service=FakeReconcileGmail(conflict["command_key"], "recipient@example.com", match_count=2),
    )
    assert ambiguous["status"] == "reconciliation_required"
    assert ambiguous["error_code"] == "gmail_marker_conflict"

    expired_lease = await create_pending_action(
        str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key="command-key-reconcile-expired-lease"
    )
    claimed, did_claim = await claim_pending_action(expired_lease["id"], owner_id)
    assert did_claim is True
    async with pending_db() as session:
        row = await session.get(PendingAction, expired_lease["id"])
        row.lease_expires_at = pending_actions._now() - timedelta(seconds=1)
        await session.commit()
    recovered = await pending_actions.reconcile_pending_action(
        expired_lease["id"],
        uuid.uuid4(),
        gmail_service=FakeReconcileGmail(
            expired_lease["command_key"], "recipient@example.com", match_count=1
        ),
    )
    assert recovered["status"] == "succeeded"
    assert recovered["result"]["external_id"] == "found-0"


class _TaskListsWithoutAcknowledgement:
    def list(self):
        return _Request(lambda: {"items": []})

    def insert(self, **_kwargs):
        return _Request(lambda: {})


class _TasksNeverCalled:
    def insert(self, **_kwargs):
        raise AssertionError("task insertion must not run without a confirmed task-list ID")


class FakeTaskListAmbiguity:
    def tasklists(self):
        return _TaskListsWithoutAcknowledgement()

    def tasks(self):
        return _TasksNeverCalled()


def test_task_list_write_without_acknowledgement_is_ambiguous():
    with pytest.raises(action_handlers.AmbiguousActionOutcome) as error:
        action_handlers._create_task_sync(
            FakeTaskListAmbiguity(),
            {"title": "Review", "notes": "Check this"},
            "command-key-task-list-ack",
        )
    assert error.value.code == "task_list_unconfirmed"


@pytest.mark.asyncio
async def test_metadata_schema_enforces_pending_action_state_checks(pending_db, owner_id):
    action = await create_pending_action(
        str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key="command-key-state-checks"
    )
    async with pending_db() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE pending_actions SET status = 'not_a_state' WHERE id = :id"),
                {"id": action["id"]},
            )
        await session.rollback()

    async with pending_db() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE pending_actions SET status = 'processing' WHERE id = :id"),
                {"id": action["id"]},
            )
        await session.rollback()


class _PagedTaskLists:
    def __init__(self, *, for_scan: bool = False):
        self.for_scan = for_scan

    def list(self, **kwargs):
        token = kwargs.get("pageToken")
        if token is None:
            first_items = [{"id": "list-first", "title": "Other"}] if self.for_scan else []
            return _Request(lambda: {"items": first_items, "nextPageToken": "next"})
        second_items = [{"id": "list-second", "title": "Other"}] if self.for_scan else [
            {"id": "list-later", "title": "Executive Agent Tasks"}
        ]
        return _Request(lambda: {"items": second_items})

    def insert(self, **_kwargs):
        raise AssertionError("a matching task list on a later page must be reused")


class _PagedTasks:
    def __init__(self, marker: str | None = None):
        self.marker = marker
        self.inserted_into = None

    def insert(self, *, tasklist, body):
        self.inserted_into = tasklist
        return _Request(lambda: {"id": "task-created"})

    def list(self, **kwargs):
        token = kwargs.get("pageToken")
        if token is None:
            return _Request(lambda: {"items": [], "nextPageToken": "next"})
        notes = f"done\n\n{self.marker}" if self.marker and kwargs["tasklist"] == "list-second" else ""
        return _Request(lambda: {"items": [{"id": "task-later", "notes": notes}]})


class FakePagedTasksService:
    def __init__(self, *, marker: str | None = None):
        self._tasklists = _PagedTaskLists(for_scan=marker is not None)
        self._tasks = _PagedTasks(marker)

    def tasklists(self):
        return self._tasklists

    def tasks(self):
        return self._tasks


def test_tasks_destination_and_reconciliation_scan_all_pages():
    dispatch_service = FakePagedTasksService()
    created = action_handlers._create_task_sync(
        dispatch_service,
        {"title": "Review", "notes": "Check this"},
        "command-key-paginated-task-list",
    )
    assert created["task_list_id"] == "list-later"
    assert dispatch_service._tasks.inserted_into == "list-later"

    marker = task_marker("command-key-paginated-scan")
    scan_service = FakePagedTasksService(marker=marker)
    assert action_handlers._scan_task_marker_sync(scan_service, marker) == [
        ({"id": "task-later", "notes": f"done\n\n{marker}"}, "list-second")
    ]


@pytest.mark.asyncio
async def test_gmail_reconciliation_requires_unique_marker_across_all_pages(
    pending_db, owner_id, immediate_google
):
    action = await create_pending_action(
        str(owner_id), ACTION_SEND_EMAIL, email_payload(), command_key="command-key-gmail-pages"
    )
    outcome = await action_handlers.reconcile_action(action, gmail_service=FakePaginatedReconcileGmail())
    assert outcome.status == "reconciliation_required"
    assert outcome.error_code == "gmail_marker_conflict"
    assert outcome.evidence == {"match_count": 2}
