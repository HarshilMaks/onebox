"""Migration regression coverage for Priority 6 pending-action expansion."""
from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from server.config import settings


@pytest.mark.asyncio
async def test_pending_action_reconciliation_migration_quarantines_legacy_processing(monkeypatch):
    configured = make_url(os.environ["DATABASE_URL"])
    database_name = f"onebox_p6_migration_{uuid.uuid4().hex}"
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
        monkeypatch.setattr(settings, "DATABASE_URL", database_url.render_as_string(hide_password=False))
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "8f8a10d8c4f1")

        engine = create_async_engine(database_url)
        async with engine.begin() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO pending_actions (
                        id, user_id, action_type, payload, payload_hash, idempotency_key,
                        summary, status, expires_at
                    ) VALUES (
                        '11111111-1111-1111-1111-111111111111',
                        '22222222-2222-2222-2222-222222222222',
                        'send_email', '{}'::json, 'legacy-hash', 'legacy-key',
                        'legacy processing action', 'processing', now() + interval '1 hour'
                    )
                    """
                )
            )
        await engine.dispose()

        command.upgrade(alembic_config, "head")
        engine = create_async_engine(database_url)
        async with engine.connect() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT command_key, status, reconciliation_reason
                        FROM pending_actions
                        WHERE id = '11111111-1111-1111-1111-111111111111'
                        """
                    )
                )
            ).one()
            constraints = (
                await db.execute(
                    text(
                        """
                        SELECT conname FROM pg_constraint
                        WHERE conrelid = 'pending_actions'::regclass
                          AND conname IN (
                            'ck_pending_actions_status',
                            'ck_pending_actions_processing_attempt',
                            'uq_pending_actions_user_command_key'
                          )
                        ORDER BY conname
                        """
                    )
                )
            ).scalars().all()
        await engine.dispose()

        assert row.command_key == "legacy:11111111-1111-1111-1111-111111111111"
        assert row.status == "reconciliation_required"
        assert row.reconciliation_reason == "legacy_processing_requires_reconciliation"
        assert constraints == [
            "ck_pending_actions_processing_attempt",
            "ck_pending_actions_status",
            "uq_pending_actions_user_command_key",
        ]
        command.downgrade(alembic_config, "8f8a10d8c4f1")
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            database_name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        await admin.close()
