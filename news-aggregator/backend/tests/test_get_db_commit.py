"""Regression: get_db must commit writes that were only flushed.

The old implementation committed only when ``session.new/dirty/deleted`` was
non-empty; after ``flush()`` those sets are empty, so PATCH /channels/{id}
(flush + refresh, no commit) returned the new values but rolled them back.
"""
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import database
from models import Source


@pytest.mark.asyncio
async def test_get_db_commits_flushed_writes(db_engine, monkeypatch):
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_maker", maker)

    gen = database.get_db()
    session = await gen.__anext__()
    session.add(Source(name="flush-only source"))
    await session.flush()  # session is now clean, like the API endpoints do
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    async with maker() as fresh:
        count = await fresh.scalar(
            select(func.count()).select_from(Source).where(Source.name == "flush-only source")
        )
    assert count == 1
