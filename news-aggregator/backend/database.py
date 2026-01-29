"""Database setup and session management.

PostgreSQL-only database configuration.
"""

import json
import os
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import cast, func, or_, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
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


def json_extract_path(column: Any, *path: str) -> ColumnElement:
    """Extract a value from a JSON column.

    Args:
        column: The JSON column to extract from
        *path: JSON path segments (e.g., 'llm_analysis', 'assigned_ak')

    Returns:
        SQLAlchemy expression for PostgreSQL jsonb_extract_path_text
    """
    return func.jsonb_extract_path_text(cast(column, JSONB), *path)


def json_array_contains(column: Any, value: str) -> ColumnElement:
    """Check if a JSON array column contains a specific value.

    Args:
        column: The JSON array column to check
        value: The value to look for in the array

    Returns:
        SQLAlchemy expression using PostgreSQL @> containment operator
    """
    json_value = json.dumps([value])
    return cast(column, JSONB).op("@>")(text(f"'{json_value}'::jsonb"))


def json_array_overlaps(column: Any, values: list[str]) -> ColumnElement:
    """Check if a JSON array column overlaps with any of the given values.

    Args:
        column: The JSON array column to check
        values: List of values to check for overlap

    Returns:
        SQLAlchemy expression using OR of containment checks
    """
    return or_(*[json_array_contains(column, v) for v in values])


# DEPRECATED: db_write_lock removed as part of concurrency optimization
# PostgreSQL handles concurrent writes via MVCC - no application-level locking needed
# See: https://github.com/claymore666/liga-hessen-news-aggregator/issues/118
#
# The previous global asyncio.Lock() was serializing ALL database writes across
# the entire application, defeating async parallelism. This was removed because:
# 1. PostgreSQL MVCC handles concurrent writes properly
# 2. The lock was causing connection starvation under load
# 3. SQLAlchemy's session-per-request model already provides isolation

# PostgreSQL connection pool settings
engine = create_async_engine(
    _get_database_url(),
    echo=settings.debug,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_pool_max_overflow,
    pool_timeout=settings.database_pool_timeout,
    pool_recycle=settings.database_pool_recycle,
    pool_pre_ping=True,  # Verify connections before use
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
        await conn.run_sync(Base.metadata.create_all)
