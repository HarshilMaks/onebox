from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from server import database, main
from server.models import Base
from server.services import readiness


def test_models_and_database_share_one_metadata_base():
    assert Base is database.Base


def test_migration_database_url_uses_sqlalchemy_url_conversion():
    converted = database.migration_database_url(
        "postgresql+asyncpg://onebox:test-password@localhost:5432/onebox"
    )
    assert converted.startswith("postgresql+psycopg2://")
    assert "asyncpg" not in converted


@pytest.mark.asyncio
async def test_readiness_fails_closed_for_database_and_redis(monkeypatch):
    monkeypatch.setattr(readiness, "settings", SimpleNamespace(requires_redis=False, runs_automation_worker=False))

    async def database_down():
        return False

    monkeypatch.setattr(readiness, "database_schema_ready", database_down)
    assert await readiness.readiness_status() == (False, "database_or_schema_unavailable")

    async def database_up():
        return True

    class RedisDown:
        async def ping(self):
            return False

    monkeypatch.setattr(readiness, "database_schema_ready", database_up)
    monkeypatch.setattr(readiness, "settings", SimpleNamespace(requires_redis=True, runs_automation_worker=False))
    monkeypatch.setattr(readiness, "get_redis_adapter", lambda: RedisDown())
    assert await readiness.readiness_status() == (False, "redis_unavailable")


@pytest.mark.asyncio
async def test_readiness_requires_persisted_automation_watch_and_worker(monkeypatch):
    owner_id = uuid4()
    monkeypatch.setattr(
        readiness,
        "settings",
        SimpleNamespace(
            requires_redis=False,
            runs_automation_worker=True,
            AUTOMATION_OWNER_ID=owner_id,
            GMAIL_WORKER_LIVENESS_SECONDS=60,
        ),
    )

    async def database_up():
        return True

    monkeypatch.setattr(readiness, "database_schema_ready", database_up)
    now = datetime.now(timezone.utc)

    async def stale_worker(_owner_id):
        return {
            "watch_valid": True,
            "watch_last_error_code": None,
            "worker_heartbeat_at": None,
            "resync_state": "idle",
            "failure_count": 0,
        }

    monkeypatch.setattr(readiness, "automation_status", stale_worker)
    assert await readiness.readiness_status() == (False, "automation_worker_stale")

    async def ready_worker(_owner_id):
        return {
            "watch_valid": True,
            "watch_last_error_code": None,
            "worker_heartbeat_at": now,
            "resync_state": "idle",
            "failure_count": 0,
        }

    monkeypatch.setattr(readiness, "automation_status", ready_worker)
    assert await readiness.readiness_status() == (True, "ready")


@pytest.mark.asyncio
async def test_livez_is_dependency_free_and_readyz_maps_readiness_to_503(monkeypatch):
    assert await main.livez() == {"status": "ok"}

    async def unavailable():
        return False, "database_or_schema_unavailable"

    monkeypatch.setattr(main, "readiness_status", unavailable)
    response = await main.readyz()
    assert response.status_code == 503
    assert b"database_or_schema_unavailable" in response.body

    async def ready():
        return True, "ready"

    monkeypatch.setattr(main, "readiness_status", ready)
    response = await main.readyz()
    assert response.status_code == 200
    assert b'"status":"ready"' in response.body
