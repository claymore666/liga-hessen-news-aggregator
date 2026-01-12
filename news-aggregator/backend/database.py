"""Database setup and session management."""

import asyncio
import os
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event, func, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.elements import ColumnElement

from config import settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


def _get_database_url() -> str:
    """Get the current database URL, respecting test overrides."""
    # Check for test database URL override first
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        return test_url
    return settings.database_url


def is_sqlite() -> bool:
    """Check if the current database is SQLite."""
    return "sqlite" in _get_database_url()


def is_postgresql() -> bool:
    """Check if the current database is PostgreSQL."""
    url = _get_database_url()
    return "postgresql" in url or "postgres" in url


def json_extract_path(column: Any, *path: str) -> ColumnElement:
    """Extract a value from a JSON column, database-agnostic.

    Args:
        column: The JSON column to extract from
        *path: JSON path segments (e.g., 'llm_analysis', 'assigned_ak')

    Returns:
        SQLAlchemy expression that works on both SQLite and PostgreSQL
    """
    if is_sqlite():
        # SQLite: json_extract(col, '$.key1.key2')
        json_path = "$." + ".".join(path)
        return func.json_extract(column, json_path)
    else:
        # PostgreSQL: Use jsonb_extract_path_text() function
        # This works with both JSON and JSONB columns
        # Cast JSON to JSONB for compatibility
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB
        return func.jsonb_extract_path_text(cast(column, JSONB), *path)


# Global lock for serializing database writes in parallel fetch scenarios
# This allows network I/O to run in parallel while database writes are serialized
db_write_lock = asyncio.Lock()

# SQLite connection options for concurrent access
connect_args: dict[str, Any] = {}
if is_sqlite():
    connect_args = {
        "timeout": 60,  # Wait up to 60 seconds for locks
        "check_same_thread": False,  # Allow multi-thread access
    }

# Use NullPool for SQLite to avoid connection pool issues
# PostgreSQL uses the default QueuePool for connection pooling
pool_class = NullPool if is_sqlite() else None

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args=connect_args,
    poolclass=pool_class,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database sessions."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Initialize database tables."""
    async with engine.begin() as conn:
        # Enable WAL mode for better concurrent access (SQLite only)
        # WAL mode allows concurrent readers with a single writer
        if is_sqlite():
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA busy_timeout=30000"))  # 30 seconds
            await conn.execute(text("PRAGMA synchronous=NORMAL"))  # Faster writes, still safe with WAL
        await conn.run_sync(Base.metadata.create_all)
