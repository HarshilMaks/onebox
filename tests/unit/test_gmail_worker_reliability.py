from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from server.routes import push_router
from server.mail import inbound as mail
from server.workers import mail_notifications


@pytest.mark.asyncio
async def test_worker_survives_claim_failure_and_retries_iteration(monkeypatch):
    stop_event = asyncio.Event()
    calls = 0

    async def heartbeat(_owner_id):
        return None

    async def renew(_owner_id):
        return False

    async def claim():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient database failure")
        stop_event.set()
        return None

    async def cleanup():
        return None

    monkeypatch.setattr(mail_notifications, "record_worker_heartbeat", heartbeat)
    monkeypatch.setattr(mail_notifications, "renew_automation_watch", renew)
    monkeypatch.setattr(mail_notifications, "claim_notification_job", claim)
    monkeypatch.setattr(mail_notifications, "prune_retained_records", cleanup)
    monkeypatch.setattr(
        mail_notifications,
        "settings",
        SimpleNamespace(
            GMAIL_RETRY_BACKOFF_INITIAL_SECONDS=0.001,
            GMAIL_NOTIFICATION_POLL_SECONDS=0.001,
            RETENTION_CLEANUP_INTERVAL_SECONDS=3600,
        ),
    )

    await mail_notifications.run_notification_worker(stop_event, uuid4())
    assert calls == 2


def test_parser_error_sentinel_is_not_analyzable_mail():
    assert mail.should_process_email({"error": "Missing payload"}) is False
    for malformed in (
        {"payload": {"headers": [], "body": {"data": "%%"}}},
        {"payload": {"headers": [], "parts": ["not-a-part"]}},
        {"payload": {"headers": [{"name": "Subject"}]}},
    ):
        assert "error" in mail.extract_email_content(malformed)


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_inbound_parser_traverses_nested_multipart_containers_to_first_nonempty_text_leaf():
    content = mail.extract_email_content(
        {
            "id": "nested-message",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Nested"},
                    {"name": "From", "value": "sender@example.test"},
                ],
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "mimeType": "multipart/related",
                        "parts": [
                            {"mimeType": "application/pdf", "body": {"data": _encoded("ignored")}},
                            {
                                "mimeType": "multipart/alternative",
                                "parts": [
                                    {"mimeType": "text/html", "body": {"data": _encoded("")}},
                                    {"mimeType": "text/plain", "body": {"data": _encoded("nested body")}},
                                ],
                            },
                        ],
                    }
                ],
            },
        }
    )

    assert content["body"] == "nested body"


def test_inbound_mime_traversal_stops_at_depth_and_part_limits():
    deep_payload: dict[str, object] = {"mimeType": "multipart/mixed", "parts": []}
    current = deep_payload
    for _ in range(mail.MAX_INBOUND_MIME_PART_DEPTH + 1):
        child: dict[str, object] = {"mimeType": "multipart/mixed", "parts": []}
        current["parts"] = [child]
        current = child
    current.update({"mimeType": "text/plain", "body": {"data": _encoded("too deep")}})

    broad_payload = {
        "mimeType": "multipart/mixed",
        "parts": [{"mimeType": "application/octet-stream"} for _ in range(mail.MAX_INBOUND_MIME_PARTS)]
        + [{"mimeType": "text/plain", "body": {"data": _encoded("too broad")}}],
    }

    assert mail.get_email_body(deep_payload) == ""
    assert mail.get_email_body(broad_payload) == ""


@pytest.mark.asyncio
async def test_automation_health_requires_watch_and_fresh_worker(monkeypatch):
    owner_id = uuid4()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        push_router,
        "settings",
        SimpleNamespace(AUTOMATION_OWNER_ID=owner_id, GMAIL_WORKER_LIVENESS_SECONDS=60),
    )

    async def status_with(*, watch_valid, heartbeat, resync_state="idle", failure_count=0, watch_error=None):
        return {
            "watch_valid": watch_valid,
            "watch_last_error_code": watch_error,
            "worker_heartbeat_at": heartbeat,
            "resync_state": resync_state,
            "failure_count": failure_count,
        }

    async def no_watch(_owner_id):
        return await status_with(watch_valid=False, heartbeat=now)

    monkeypatch.setattr(push_router, "automation_status", no_watch)
    degraded = await push_router.health_check(_operator={})
    assert degraded["status"] == "degraded"
    assert "watch" in degraded["detail"].lower()

    async def stale_worker(_owner_id):
        return await status_with(watch_valid=True, heartbeat=now - timedelta(seconds=61))

    monkeypatch.setattr(push_router, "automation_status", stale_worker)
    degraded = await push_router.health_check(_operator={})
    assert degraded["status"] == "degraded"
    assert "heartbeat" in degraded["detail"].lower()

    async def watch_failure(_owner_id):
        return await status_with(watch_valid=True, heartbeat=now, watch_error="watch_provider_unavailable")

    monkeypatch.setattr(push_router, "automation_status", watch_failure)
    degraded = await push_router.health_check(_operator={})
    assert degraded["status"] == "degraded"
    assert "renewal" in degraded["detail"].lower()

    async def healthy(_owner_id):
        return await status_with(watch_valid=True, heartbeat=now)

    monkeypatch.setattr(push_router, "automation_status", healthy)
    ready = await push_router.health_check(_operator={})
    assert ready["status"] == "healthy"
    assert ready["gmail_service_status"] == "ready"


def test_compose_defines_dedicated_durable_worker():
    compose = open("docker-compose.yaml", encoding="utf-8").read()
    assert "  worker:\n" in compose
    assert 'command: ["python", "-m", "server.workers"]' in compose
    assert "SERVICE_ROLE: automation_worker" in compose
    assert "restart: unless-stopped" in compose
    assert "service_completed_successfully" in compose
