"""Tests for the scheduler service."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, Source, ConnectorType


@pytest.fixture
async def channels_with_intervals(db_session: AsyncSession):
    """Create test channels with different fetch intervals and last_fetch_at."""
    now = datetime.utcnow()

    # Create sources first
    source_active = Source(name="Active Source", enabled=True)
    source_disabled = Source(name="Disabled Source", enabled=False)
    db_session.add_all([source_active, source_disabled])
    await db_session.flush()

    # Channel 1: Due (last fetch was 2 hours ago, interval is 60 min)
    channel_due = Channel(
        source_id=source_active.id,
        name="Due Channel",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/due.xml"},
        enabled=True,
        fetch_interval_minutes=60,
        last_fetch_at=now - timedelta(hours=2),
    )

    # Channel 2: Not due (last fetch was 30 min ago, interval is 60 min)
    channel_not_due = Channel(
        source_id=source_active.id,
        name="Not Due Channel",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/not-due.xml"},
        enabled=True,
        fetch_interval_minutes=60,
        last_fetch_at=now - timedelta(minutes=30),
    )

    # Channel 3: Never fetched (NULL last_fetch_at) - always due
    channel_never_fetched = Channel(
        source_id=source_active.id,
        name="Never Fetched Channel",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/never.xml"},
        enabled=True,
        fetch_interval_minutes=60,
        last_fetch_at=None,
    )

    # Channel 4: Disabled channel (should never be fetched)
    channel_disabled = Channel(
        source_id=source_active.id,
        name="Disabled Channel",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/disabled.xml"},
        enabled=False,
        fetch_interval_minutes=60,
        last_fetch_at=now - timedelta(hours=24),
    )

    # Channel 5: Enabled but parent source is disabled
    channel_disabled_source = Channel(
        source_id=source_disabled.id,
        name="Disabled Source Channel",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/disabled-source.xml"},
        enabled=True,
        fetch_interval_minutes=60,
        last_fetch_at=now - timedelta(hours=24),
    )

    db_session.add_all([
        channel_due, channel_not_due, channel_never_fetched,
        channel_disabled, channel_disabled_source
    ])
    await db_session.commit()

    return {
        "due": channel_due,
        "not_due": channel_not_due,
        "never_fetched": channel_never_fetched,
        "disabled": channel_disabled,
        "disabled_source": channel_disabled_source,
    }


class TestFetchDueChannels:
    """Tests for fetch_due_channels function."""

    @pytest.mark.asyncio
    async def test_identifies_due_channels(self, db_session: AsyncSession, channels_with_intervals):
        """Test that due channels are correctly identified."""
        from services.scheduler import fetch_due_channels

        # Mock fetch_channel to track which channels are fetched
        fetched_ids = []

        async def mock_fetch_channel(channel_id, training_mode=False):
            fetched_ids.append(channel_id)
            return 0

        # Patch the database session and fetch_channel
        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", mock_fetch_channel):

            # Setup mock session context manager
            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_due_channels()

        # Should have fetched "due" and "never_fetched" channels
        assert result["due_channels"] == 2
        assert channels_with_intervals["due"].id in fetched_ids
        assert channels_with_intervals["never_fetched"].id in fetched_ids
        assert channels_with_intervals["not_due"].id not in fetched_ids
        assert channels_with_intervals["disabled"].id not in fetched_ids
        assert channels_with_intervals["disabled_source"].id not in fetched_ids

    @pytest.mark.asyncio
    async def test_skips_disabled_channels(self, db_session: AsyncSession, channels_with_intervals):
        """Test that disabled channels are never fetched."""
        from services.scheduler import fetch_due_channels

        fetched_ids = []

        async def mock_fetch_channel(channel_id, training_mode=False):
            fetched_ids.append(channel_id)
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", mock_fetch_channel):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            await fetch_due_channels()

        # Disabled channel and channel with disabled source should never be fetched
        assert channels_with_intervals["disabled"].id not in fetched_ids
        assert channels_with_intervals["disabled_source"].id not in fetched_ids

    @pytest.mark.asyncio
    async def test_null_last_fetch_always_due(self, db_session: AsyncSession, channels_with_intervals):
        """Test that channels with NULL last_fetch_at are always considered due."""
        from services.scheduler import fetch_due_channels

        fetched_ids = []

        async def mock_fetch_channel(channel_id, training_mode=False):
            fetched_ids.append(channel_id)
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", mock_fetch_channel):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            await fetch_due_channels()

        # Never fetched channel should always be due
        assert channels_with_intervals["never_fetched"].id in fetched_ids

    @pytest.mark.asyncio
    async def test_fetch_in_progress_skips(self):
        """Test that concurrent fetches are skipped."""
        import services.scheduler as scheduler_module
        from services.scheduler import fetch_due_channels

        # Simulate a fetch already in progress
        original_flag = scheduler_module._fetch_in_progress
        scheduler_module._fetch_in_progress = True

        try:
            result = await fetch_due_channels()
            assert result.get("skipped") is True
            assert result.get("reason") == "fetch_in_progress"
        finally:
            scheduler_module._fetch_in_progress = original_flag

    @pytest.mark.asyncio
    async def test_handles_fetch_errors(self, db_session: AsyncSession, channels_with_intervals):
        """Test that errors during fetch don't stop other channels."""
        from services.scheduler import fetch_due_channels

        call_count = 0

        async def mock_fetch_channel_with_error(channel_id, training_mode=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Test error")
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", mock_fetch_channel_with_error):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_due_channels()

        # Should have 1 error and 1 success (2 due channels)
        assert result["errors"] == 1
        assert result["fetched"] == 1

    @pytest.mark.asyncio
    async def test_fetches_oldest_first(self, db_session: AsyncSession):
        """Test that channels are fetched oldest first."""
        now = datetime.utcnow()

        # Create source
        source = Source(name="Test Source", enabled=True)
        db_session.add(source)
        await db_session.flush()

        # Create channels with different ages
        channel_oldest = Channel(
            source_id=source.id,
            name="Oldest",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/oldest.xml"},
            enabled=True,
            fetch_interval_minutes=60,
            last_fetch_at=now - timedelta(hours=10),
        )
        channel_newer = Channel(
            source_id=source.id,
            name="Newer",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/newer.xml"},
            enabled=True,
            fetch_interval_minutes=60,
            last_fetch_at=now - timedelta(hours=5),
        )

        db_session.add_all([channel_newer, channel_oldest])  # Add in wrong order
        await db_session.commit()

        from services.scheduler import fetch_due_channels

        fetch_order = []

        async def mock_fetch_channel(channel_id, training_mode=False):
            fetch_order.append(channel_id)
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", mock_fetch_channel):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            await fetch_due_channels()

        # Oldest should be fetched first
        assert fetch_order[0] == channel_oldest.id
        assert fetch_order[1] == channel_newer.id


# Backward compatibility alias tests
class TestFetchDueSources:
    """Tests to ensure backward compatibility alias works."""

    @pytest.mark.asyncio
    async def test_fetch_due_sources_alias_exists(self):
        """Test that fetch_due_sources is an alias to fetch_due_channels."""
        from services.scheduler import fetch_due_sources, fetch_due_channels
        assert fetch_due_sources is fetch_due_channels
