"""Tests for the scheduler service."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Source, ConnectorType


@pytest.fixture
async def sources_with_intervals(db_session: AsyncSession):
    """Create test sources with different fetch intervals and last_fetch_at."""
    now = datetime.utcnow()

    # Source 1: Due (last fetch was 2 hours ago, interval is 60 min)
    source_due = Source(
        name="Due Source",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/due.xml"},
        enabled=True,
        fetch_interval_minutes=60,
        last_fetch_at=now - timedelta(hours=2),
    )

    # Source 2: Not due (last fetch was 30 min ago, interval is 60 min)
    source_not_due = Source(
        name="Not Due Source",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/not-due.xml"},
        enabled=True,
        fetch_interval_minutes=60,
        last_fetch_at=now - timedelta(minutes=30),
    )

    # Source 3: Never fetched (NULL last_fetch_at) - always due
    source_never_fetched = Source(
        name="Never Fetched Source",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/never.xml"},
        enabled=True,
        fetch_interval_minutes=60,
        last_fetch_at=None,
    )

    # Source 4: Disabled (should never be fetched)
    source_disabled = Source(
        name="Disabled Source",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/disabled.xml"},
        enabled=False,
        fetch_interval_minutes=60,
        last_fetch_at=now - timedelta(hours=24),
    )

    db_session.add_all([source_due, source_not_due, source_never_fetched, source_disabled])
    await db_session.commit()

    return {
        "due": source_due,
        "not_due": source_not_due,
        "never_fetched": source_never_fetched,
        "disabled": source_disabled,
    }


class TestFetchDueSources:
    """Tests for fetch_due_sources function."""

    @pytest.mark.asyncio
    async def test_identifies_due_sources(self, db_session: AsyncSession, sources_with_intervals):
        """Test that due sources are correctly identified."""
        from services.scheduler import fetch_due_sources

        # Mock fetch_source to track which sources are fetched
        fetched_ids = []

        async def mock_fetch_source(source_id, training_mode=False):
            fetched_ids.append(source_id)
            return 0

        # Patch the database session and fetch_source
        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_source", mock_fetch_source):

            # Setup mock session context manager
            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_due_sources()

        # Should have fetched "due" and "never_fetched" sources
        assert result["due_sources"] == 2
        assert sources_with_intervals["due"].id in fetched_ids
        assert sources_with_intervals["never_fetched"].id in fetched_ids
        assert sources_with_intervals["not_due"].id not in fetched_ids
        assert sources_with_intervals["disabled"].id not in fetched_ids

    @pytest.mark.asyncio
    async def test_skips_disabled_sources(self, db_session: AsyncSession, sources_with_intervals):
        """Test that disabled sources are never fetched."""
        from services.scheduler import fetch_due_sources

        fetched_ids = []

        async def mock_fetch_source(source_id, training_mode=False):
            fetched_ids.append(source_id)
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_source", mock_fetch_source):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            await fetch_due_sources()

        # Disabled source should never be fetched
        assert sources_with_intervals["disabled"].id not in fetched_ids

    @pytest.mark.asyncio
    async def test_null_last_fetch_always_due(self, db_session: AsyncSession, sources_with_intervals):
        """Test that sources with NULL last_fetch_at are always considered due."""
        from services.scheduler import fetch_due_sources

        fetched_ids = []

        async def mock_fetch_source(source_id, training_mode=False):
            fetched_ids.append(source_id)
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_source", mock_fetch_source):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            await fetch_due_sources()

        # Never fetched source should always be due
        assert sources_with_intervals["never_fetched"].id in fetched_ids

    @pytest.mark.asyncio
    async def test_fetch_in_progress_skips(self):
        """Test that concurrent fetches are skipped."""
        import services.scheduler as scheduler_module
        from services.scheduler import fetch_due_sources

        # Simulate a fetch already in progress
        original_flag = scheduler_module._fetch_in_progress
        scheduler_module._fetch_in_progress = True

        try:
            result = await fetch_due_sources()
            assert result.get("skipped") is True
            assert result.get("reason") == "fetch_in_progress"
        finally:
            scheduler_module._fetch_in_progress = original_flag

    @pytest.mark.asyncio
    async def test_handles_fetch_errors(self, db_session: AsyncSession, sources_with_intervals):
        """Test that errors during fetch don't stop other sources."""
        from services.scheduler import fetch_due_sources

        call_count = 0

        async def mock_fetch_source_with_error(source_id, training_mode=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Test error")
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_source", mock_fetch_source_with_error):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_due_sources()

        # Should have 1 error and 1 success (2 due sources)
        assert result["errors"] == 1
        assert result["fetched"] == 1

    @pytest.mark.asyncio
    async def test_fetches_oldest_first(self, db_session: AsyncSession):
        """Test that sources are fetched oldest first."""
        now = datetime.utcnow()

        # Create sources with different ages
        source_oldest = Source(
            name="Oldest",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/oldest.xml"},
            enabled=True,
            fetch_interval_minutes=60,
            last_fetch_at=now - timedelta(hours=10),
        )
        source_newer = Source(
            name="Newer",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/newer.xml"},
            enabled=True,
            fetch_interval_minutes=60,
            last_fetch_at=now - timedelta(hours=5),
        )

        db_session.add_all([source_newer, source_oldest])  # Add in wrong order
        await db_session.commit()

        from services.scheduler import fetch_due_sources

        fetch_order = []

        async def mock_fetch_source(source_id, training_mode=False):
            fetch_order.append(source_id)
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_source", mock_fetch_source):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            await fetch_due_sources()

        # Oldest should be fetched first
        assert fetch_order[0] == source_oldest.id
        assert fetch_order[1] == source_newer.id
