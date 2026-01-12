"""Database setup and session management."""

import asyncio
from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from config import settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


# Global lock for serializing database writes in parallel fetch scenarios
# This allows network I/O to run in parallel while database writes are serialized
db_write_lock = asyncio.Lock()

# SQLite connection options for concurrent access
# Using StaticPool with a single connection to avoid lock contention
connect_args = {}
if "sqlite" in settings.database_url:
    connect_args = {
        "timeout": 60,  # Wait up to 60 seconds for locks
        "check_same_thread": False,  # Allow multi-thread access
    }

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args=connect_args,
    # Use NullPool for SQLite to avoid connection pool issues
    # Each session will create and close its own connection
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
        # Enable WAL mode for better concurrent access
        # WAL mode allows concurrent readers with a single writer
        if "sqlite" in settings.database_url:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA busy_timeout=30000"))  # 30 seconds
            await conn.execute(text("PRAGMA synchronous=NORMAL"))  # Faster writes, still safe with WAL
        await conn.run_sync(Base.metadata.create_all)
