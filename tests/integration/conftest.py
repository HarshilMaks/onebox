"""Shared integration fixtures."""
from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from server.models import Base
from server.services import pending_actions, triage_results


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
        monkeypatch.setattr(triage_results, "AsyncSessionLocal", sessions)
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
