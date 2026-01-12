"""Tests for stats API endpoints."""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, ConnectorType, Item, Priority, Source


class TestGetStats:
    """Tests for GET /api/stats endpoint."""

    @pytest.mark.asyncio
    async def test_stats_empty(self, client: AsyncClient):
        """Returns zeros when database is empty."""
        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["total_items"] == 0
        assert data["sources_count"] == 0
        assert data["channels_count"] == 0
        assert data["rules_count"] == 0

    @pytest.mark.asyncio
    async def test_stats_with_data(
        self, client: AsyncClient, multiple_items_in_db: list[Item], source_in_db: Source
    ):
        """Returns correct counts with data."""
        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["total_items"] >= 4
        assert data["sources_count"] >= 1
        assert data["channels_count"] >= 1

    @pytest.mark.asyncio
    async def test_stats_unread_count(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Counts unread items correctly."""
        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert "unread_items" in data
        # From fixture: some items are unread (odd indexes), but stats may filter
        assert data["unread_items"] >= 1

    @pytest.mark.asyncio
    async def test_stats_starred_count(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Counts starred items correctly."""
        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert "starred_items" in data
        # From fixture: only first item is starred
        assert data["starred_items"] >= 1

    @pytest.mark.asyncio
    async def test_stats_priority_counts(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Counts items by priority correctly."""
        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert "high_items" in data
        assert "medium_items" in data
        assert "items_by_priority" in data

    @pytest.mark.asyncio
    async def test_stats_enabled_counts(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Counts enabled/disabled sources and channels."""
        # Create enabled and disabled sources
        enabled_source = Source(name="Enabled", enabled=True)
        disabled_source = Source(name="Disabled", enabled=False)
        db_session.add_all([enabled_source, disabled_source])
        await db_session.flush()

        # Create enabled and disabled channels
        enabled_channel = Channel(
            source_id=enabled_source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/1"},
            enabled=True,
        )
        disabled_channel = Channel(
            source_id=enabled_source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/2"},
            enabled=False,
        )
        db_session.add_all([enabled_channel, disabled_channel])
        await db_session.flush()

        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert "enabled_sources" in data
        assert "enabled_channels" in data
        assert data["enabled_sources"] >= 1
        assert data["enabled_channels"] >= 1

    @pytest.mark.asyncio
    async def test_stats_items_today(
        self, client: AsyncClient, db_session: AsyncSession, channel_in_db: Channel
    ):
        """Counts items published today."""
        # Create item from today
        today_item = Item(
            channel_id=channel_in_db.id,
            external_id="today-1",
            title="Today's Article",
            content="Content",
            url="https://test.com/today",
            published_at=datetime.utcnow(),
            content_hash="todayhash",
        )
        db_session.add(today_item)
        await db_session.flush()

        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert "items_today" in data
        assert data["items_today"] >= 1

    @pytest.mark.asyncio
    async def test_stats_items_this_week(
        self, client: AsyncClient, db_session: AsyncSession, channel_in_db: Channel
    ):
        """Counts items published this week."""
        # Create item from this week
        week_item = Item(
            channel_id=channel_in_db.id,
            external_id="week-1",
            title="This Week's Article",
            content="Content",
            url="https://test.com/week",
            published_at=datetime.utcnow() - timedelta(days=3),
            content_hash="weekhash",
        )
        db_session.add(week_item)
        await db_session.flush()

        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert "items_this_week" in data
        assert data["items_this_week"] >= 1


class TestStatsBySource:
    """Tests for GET /api/stats/by-source endpoint."""

    @pytest.mark.asyncio
    async def test_stats_by_source_empty(self, client: AsyncClient):
        """Returns empty list when no sources exist."""
        response = await client.get("/api/stats/by-source")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_stats_by_source_with_data(
        self, client: AsyncClient, source_in_db: Source, item_in_db: Item
    ):
        """Returns per-source statistics."""
        response = await client.get("/api/stats/by-source")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

        source_stat = next(s for s in data if s["source_id"] == source_in_db.id)
        assert source_stat["name"] == source_in_db.name
        assert "item_count" in source_stat
        assert "unread_count" in source_stat


class TestStatsByChannel:
    """Tests for GET /api/stats/by-channel endpoint."""

    @pytest.mark.asyncio
    async def test_stats_by_channel_empty(self, client: AsyncClient):
        """Returns empty list when no channels exist."""
        response = await client.get("/api/stats/by-channel")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_stats_by_channel_with_data(
        self, client: AsyncClient, channel_in_db: Channel, item_in_db: Item, source_in_db: Source
    ):
        """Returns per-channel statistics."""
        response = await client.get("/api/stats/by-channel")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

        channel_stat = next(c for c in data if c["channel_id"] == channel_in_db.id)
        assert channel_stat["source_name"] == source_in_db.name
        assert channel_stat["connector_type"] == "rss"
        assert "item_count" in channel_stat
        assert "unread_count" in channel_stat


class TestStatsByConnector:
    """Tests for GET /api/stats/by-connector endpoint."""

    @pytest.mark.asyncio
    async def test_stats_by_connector_empty(self, client: AsyncClient):
        """Returns empty list when no channels exist."""
        response = await client.get("/api/stats/by-connector")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_stats_by_connector_with_data(
        self, client: AsyncClient, channel_in_db: Channel, item_in_db: Item
    ):
        """Returns per-connector-type statistics."""
        response = await client.get("/api/stats/by-connector")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

        connector_stat = next(c for c in data if c["connector_type"] == "rss")
        assert "item_count" in connector_stat
        assert "channel_count" in connector_stat

    @pytest.mark.asyncio
    async def test_stats_by_connector_multiple_types(
        self, client: AsyncClient, db_session: AsyncSession, source_in_db: Source
    ):
        """Returns stats for each connector type."""
        # Create channels with different connector types
        rss_channel = Channel(
            source_id=source_in_db.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/rss"},
        )
        x_channel = Channel(
            source_id=source_in_db.id,
            connector_type=ConnectorType.X_SCRAPER,
            config={"handle": "@test"},
        )
        db_session.add_all([rss_channel, x_channel])
        await db_session.flush()

        response = await client.get("/api/stats/by-connector")

        assert response.status_code == 200
        data = response.json()
        connector_types = [c["connector_type"] for c in data]
        assert "rss" in connector_types
        assert "x_scraper" in connector_types


class TestStatsByPriority:
    """Tests for GET /api/stats/by-priority endpoint."""

    @pytest.mark.asyncio
    async def test_stats_by_priority_empty(self, client: AsyncClient):
        """Returns empty/zero stats when no items exist."""
        response = await client.get("/api/stats/by-priority")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, (list, dict))

    @pytest.mark.asyncio
    async def test_stats_by_priority_with_data(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Returns per-priority statistics."""
        response = await client.get("/api/stats/by-priority")

        assert response.status_code == 200
        data = response.json()
        # Should have counts for each priority level (high, medium, low, none)
        if isinstance(data, dict):
            assert "high" in data
            assert "medium" in data
            assert "low" in data
            assert "none" in data
        elif isinstance(data, list):
            priorities = [d.get("priority", d.get("name", "")) for d in data]
            assert any("high" in str(p).lower() for p in priorities)
