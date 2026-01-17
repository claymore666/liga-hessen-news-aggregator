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
    return settings.get_database_url()


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


def json_array_contains(column: Any, value: str) -> ColumnElement:
    """Check if a JSON array column contains a specific value.

    Args:
        column: The JSON array column to check
        value: The value to look for in the array

    Returns:
        SQLAlchemy expression that works on both SQLite and PostgreSQL
    """
    import json
    if is_sqlite():
        # SQLite: Use LIKE pattern matching on JSON string
        # This works because JSON arrays are stored as '["AK1","AK2"]'
        return column.like(f'%"{value}"%')
    else:
        # PostgreSQL: Use @> containment operator with JSONB
        # Use text() with bindparam for proper JSON encoding
        from sqlalchemy import cast, text, bindparam
        from sqlalchemy.dialects.postgresql import JSONB
        # Cast column to JSONB and use @> with a JSON-encoded array literal
        json_value = json.dumps([value])
        return cast(column, JSONB).op("@>")(text(f"'{json_value}'::jsonb"))


def json_array_overlaps(column: Any, values: list[str]) -> ColumnElement:
    """Check if a JSON array column overlaps with any of the given values.

    Args:
        column: The JSON array column to check
        values: List of values to check for overlap

    Returns:
        SQLAlchemy expression that works on both SQLite and PostgreSQL
    """
    from sqlalchemy import or_
    if is_sqlite():
        # SQLite: Use OR with LIKE for each value
        return or_(*[json_array_contains(column, v) for v in values])
    else:
        # PostgreSQL: Use multiple containment checks with OR
        return or_(*[json_array_contains(column, v) for v in values])


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
# PostgreSQL uses QueuePool with configurable settings
pool_class = NullPool if is_sqlite() else None

# Build engine kwargs based on database type
engine_kwargs: dict[str, Any] = {
    "echo": settings.debug,
    "connect_args": connect_args,
}

if pool_class:
    engine_kwargs["poolclass"] = pool_class
else:
    # PostgreSQL: use connection pool settings from config
    engine_kwargs["pool_size"] = settings.database_pool_size
    engine_kwargs["max_overflow"] = settings.database_pool_max_overflow
    engine_kwargs["pool_timeout"] = settings.database_pool_timeout
    engine_kwargs["pool_recycle"] = settings.database_pool_recycle
    engine_kwargs["pool_pre_ping"] = True  # Verify connections before use

engine = create_async_engine(_get_database_url(), **engine_kwargs)

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
