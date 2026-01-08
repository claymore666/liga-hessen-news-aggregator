"""Tests for channel API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, ConnectorType, Source


class TestChannelsAPI:
    """Tests for channel CRUD operations."""

    @pytest.mark.asyncio
    async def test_add_channel_to_source(self, client: AsyncClient, db_session: AsyncSession):
        """POST /api/sources/{id}/channels creates channel."""
        # Create source first
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel_data = {
            "name": "Main Feed",
            "connector_type": "rss",
            "config": {"url": "https://example.com/feed.xml"},
            "enabled": True,
            "fetch_interval_minutes": 30,
        }

        response = await client.post(f"/api/sources/{source.id}/channels", json=channel_data)

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Main Feed"
        assert data["connector_type"] == "rss"
        assert data["source_id"] == source.id

    @pytest.mark.asyncio
    async def test_add_duplicate_channel_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Adding channel with same connector+identifier returns 409."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        # Add first channel
        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
            source_identifier="https://example.com/feed.xml",
        )
        db_session.add(channel)
        await db_session.flush()

        # Try to add duplicate
        channel_data = {
            "connector_type": "rss",
            "config": {"url": "https://example.com/feed.xml"},
        }

        response = await client.post(f"/api/sources/{source.id}/channels", json=channel_data)

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_get_channel(self, client: AsyncClient, db_session: AsyncSession):
        """GET /api/channels/{id} returns channel."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Feed",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
        )
        db_session.add(channel)
        await db_session.flush()

        response = await client.get(f"/api/channels/{channel.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == channel.id
        assert data["name"] == "Test Feed"

    @pytest.mark.asyncio
    async def test_update_channel(self, client: AsyncClient, db_session: AsyncSession):
        """PATCH /api/channels/{id} updates channel."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Old Name",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
            fetch_interval_minutes=30,
        )
        db_session.add(channel)
        await db_session.flush()

        update_data = {"name": "New Name", "fetch_interval_minutes": 60}

        response = await client.patch(f"/api/channels/{channel.id}", json=update_data)

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Name"
        assert data["fetch_interval_minutes"] == 60

    @pytest.mark.asyncio
    async def test_delete_channel(self, client: AsyncClient, db_session: AsyncSession):
        """DELETE /api/channels/{id} removes channel."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
        )
        db_session.add(channel)
        await db_session.flush()

        response = await client.delete(f"/api/channels/{channel.id}")

        assert response.status_code == 204

        # Verify deleted
        response = await client.get(f"/api/channels/{channel.id}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_enable_channel(self, client: AsyncClient, db_session: AsyncSession):
        """POST /api/channels/{id}/enable enables channel."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
            enabled=False,
        )
        db_session.add(channel)
        await db_session.flush()

        response = await client.post(f"/api/channels/{channel.id}/enable")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True

    @pytest.mark.asyncio
    async def test_disable_channel(self, client: AsyncClient, db_session: AsyncSession):
        """POST /api/channels/{id}/disable disables channel."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
            enabled=True,
        )
        db_session.add(channel)
        await db_session.flush()

        response = await client.post(f"/api/channels/{channel.id}/disable")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_sources_include_channels(self, client: AsyncClient, db_session: AsyncSession):
        """GET /api/sources returns nested channels."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel1 = Channel(
            source_id=source.id,
            name="RSS Feed",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
        )
        channel2 = Channel(
            source_id=source.id,
            name="X Account",
            connector_type=ConnectorType.X_SCRAPER,
            config={"handle": "@example"},
        )
        db_session.add_all([channel1, channel2])
        await db_session.flush()

        response = await client.get("/api/sources")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert len(data[0]["channels"]) == 2
        assert data[0]["channel_count"] == 2

    @pytest.mark.asyncio
    async def test_source_with_multiple_rss_channels(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test source with multiple RSS channels (FAZ case)."""
        source = Source(name="FAZ", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channels_data = [
            {"name": "Aktuell", "connector_type": "rss", "config": {"url": "https://faz.net/aktuell.rss"}},
            {"name": "Gesellschaft", "connector_type": "rss", "config": {"url": "https://faz.net/gesellschaft.rss"}},
            {"name": "Rhein-Main", "connector_type": "rss", "config": {"url": "https://faz.net/rhein-main.rss"}},
        ]

        for channel_data in channels_data:
            response = await client.post(f"/api/sources/{source.id}/channels", json=channel_data)
            assert response.status_code == 201

        # Verify all channels created
        response = await client.get(f"/api/sources/{source.id}")
        assert response.status_code == 200
        data = response.json()
        assert len(data["channels"]) == 3
        assert all(c["connector_type"] == "rss" for c in data["channels"])


class TestSourcesAPI:
    """Tests for source (organization) endpoints."""

    @pytest.mark.asyncio
    async def test_create_source_without_channels(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """POST /api/sources creates organization."""
        source_data = {
            "name": "Test Organization",
            "description": "A test org",
            "is_stakeholder": True,
            "enabled": True,
        }

        response = await client.post("/api/sources", json=source_data)

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Organization"
        assert data["is_stakeholder"] is True
        assert data["channels"] == []

    @pytest.mark.asyncio
    async def test_create_source_with_channels(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """POST /api/sources with initial channels."""
        source_data = {
            "name": "BMAS",
            "enabled": True,
            "channels": [
                {
                    "connector_type": "rss",
                    "config": {"url": "https://bmas.de/feed.xml"},
                    "fetch_interval_minutes": 30,
                },
                {
                    "connector_type": "x_scraper",
                    "config": {"handle": "@BMAS_Bund"},
                    "fetch_interval_minutes": 60,
                },
            ],
        }

        response = await client.post("/api/sources", json=source_data)

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "BMAS"
        assert len(data["channels"]) == 2
        assert data["channel_count"] == 2

    @pytest.mark.asyncio
    async def test_update_source(self, client: AsyncClient, db_session: AsyncSession):
        """PATCH /api/sources/{id} updates organization fields."""
        source = Source(name="Old Name", enabled=True)
        db_session.add(source)
        await db_session.flush()

        update_data = {"name": "New Name", "is_stakeholder": True}

        response = await client.patch(f"/api/sources/{source.id}", json=update_data)

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Name"
        assert data["is_stakeholder"] is True

    @pytest.mark.asyncio
    async def test_enable_source(self, client: AsyncClient, db_session: AsyncSession):
        """POST /api/sources/{id}/enable enables source."""
        source = Source(name="Test", enabled=False)
        db_session.add(source)
        await db_session.flush()

        response = await client.post(f"/api/sources/{source.id}/enable")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True

    @pytest.mark.asyncio
    async def test_disable_source(self, client: AsyncClient, db_session: AsyncSession):
        """POST /api/sources/{id}/disable disables source."""
        source = Source(name="Test", enabled=True)
        db_session.add(source)
        await db_session.flush()

        response = await client.post(f"/api/sources/{source.id}/disable")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_delete_source_cascades(self, client: AsyncClient, db_session: AsyncSession):
        """DELETE /api/sources/{id} removes source and channels."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
        )
        db_session.add(channel)
        await db_session.flush()
        channel_id = channel.id

        response = await client.delete(f"/api/sources/{source.id}")

        assert response.status_code == 204

        # Verify source and channel deleted
        response = await client.get(f"/api/sources/{source.id}")
        assert response.status_code == 404

        response = await client.get(f"/api/channels/{channel_id}")
        assert response.status_code == 404


class TestStatsAPI:
    """Tests for stats endpoints."""

    @pytest.mark.asyncio
    async def test_stats_include_channel_counts(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """GET /api/stats includes channel counts."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
            enabled=True,
        )
        db_session.add(channel)
        await db_session.flush()

        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert "channels_count" in data
        assert "enabled_channels" in data
        assert data["channels_count"] == 1
        assert data["enabled_channels"] == 1

    @pytest.mark.asyncio
    async def test_stats_by_channel(self, client: AsyncClient, db_session: AsyncSession):
        """GET /api/stats/by-channel returns channel stats."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Feed",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
        )
        db_session.add(channel)
        await db_session.flush()

        response = await client.get("/api/stats/by-channel")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["source_name"] == "Test Org"
        assert data[0]["name"] == "Test Feed"

    @pytest.mark.asyncio
    async def test_stats_by_connector(self, client: AsyncClient, db_session: AsyncSession):
        """GET /api/stats/by-connector returns connector type stats."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel1 = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
        )
        channel2 = Channel(
            source_id=source.id,
            connector_type=ConnectorType.X_SCRAPER,
            config={"handle": "@example"},
        )
        db_session.add_all([channel1, channel2])
        await db_session.flush()

        response = await client.get("/api/stats/by-connector")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        connector_types = [d["connector_type"] for d in data]
        assert "rss" in connector_types
        assert "x_scraper" in connector_types
