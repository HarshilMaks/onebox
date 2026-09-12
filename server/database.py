from __future__ import annotations

from functools import lru_cache

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from server.config import PROJECT_ROOT, settings


Base = declarative_base()


def migration_database_url(database_url: str) -> str:
    """Return Alembic's synchronous PostgreSQL URL without string substitution."""
    url = make_url(database_url)
    if url.drivername == "postgresql+asyncpg":
        url = url.set(drivername="postgresql+psycopg2")
    return url.render_as_string(hide_password=False)


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_timeout=settings.DATABASE_CONNECT_TIMEOUT_SECONDS,
    connect_args={"timeout": settings.DATABASE_CONNECT_TIMEOUT_SECONDS},
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@lru_cache(maxsize=1)
def migration_head() -> str:
    """Read the repository migration head once; this is filesystem-only work."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("Alembic migration head is unavailable")
    return head


async def database_schema_ready() -> bool:
    """Return true only when PostgreSQL is reachable and exactly at Alembic head."""
    try:
        expected_head = migration_head()
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    except (RuntimeError, SQLAlchemyError):
        return False
    return version == expected_head


async def close_database() -> None:
    await engine.dispose()


async def get_agent_db():
    async with AsyncSessionLocal() as session:
        yield session
