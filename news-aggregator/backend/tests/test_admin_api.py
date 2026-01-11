"""Tests for admin API endpoints."""

from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, ConnectorType, Item, Priority, Rule, RuleType, Source


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    @pytest.mark.asyncio
    async def test_basic_health_check(self, client: AsyncClient):
        """GET /health returns basic healthy status."""
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    @pytest.mark.asyncio
    async def test_detailed_health_check(self, client: AsyncClient):
        """GET /api/admin/health returns detailed status."""
        response = await client.get("/api/admin/health")

        assert response.status_code == 200
        data = response.json()

        # Check required fields
        assert "status" in data
        assert "instance_type" in data
        assert "llm_enabled" in data
        assert "scheduler_running" in data
        assert "scheduler_jobs" in data
        assert "llm_available" in data
        assert "database_ok" in data
        assert "items_count" in data
        assert "sources_count" in data

    @pytest.mark.asyncio
    async def test_health_check_database_counts(
        self, client: AsyncClient, source_in_db: Source, item_in_db: Item
    ):
        """Health check reflects database item counts."""
        response = await client.get("/api/admin/health")

        assert response.status_code == 200
        data = response.json()
        assert data["database_ok"] is True
        assert data["items_count"] >= 1
        assert data["sources_count"] >= 1


class TestDatabaseStatsEndpoint:
    """Tests for /api/admin/db-stats endpoint."""

    @pytest.mark.asyncio
    async def test_db_stats_empty(self, client: AsyncClient):
        """GET /api/admin/db-stats with empty database."""
        response = await client.get("/api/admin/db-stats")

        assert response.status_code == 200
        data = response.json()
        assert data["items_count"] == 0
        assert data["sources_count"] == 0
        assert data["rules_count"] == 0
        assert data["items_with_summary"] == 0
        assert data["items_without_summary"] == 0

    @pytest.mark.asyncio
    async def test_db_stats_with_data(
        self, client: AsyncClient, db_session: AsyncSession, source_in_db: Source
    ):
        """GET /api/admin/db-stats reflects actual data."""
        # Add channel and items
        channel = Channel(
            source_id=source_in_db.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/feed"},
        )
        db_session.add(channel)
        await db_session.flush()

        # Add items - some with summary, some without
        for i in range(5):
            item = Item(
                channel_id=channel.id,
                external_id=f"ext-{i}",
                title=f"Article {i}",
                content="Content",
                url=f"https://test.com/{i}",
                published_at=datetime.utcnow(),
                content_hash=f"hash{i}",
                summary="Summary" if i < 2 else None,
            )
            db_session.add(item)
        await db_session.flush()

        # Add a rule
        rule = Rule(
            name="Test Rule",
            rule_type=RuleType.KEYWORD,
            pattern="test",
        )
        db_session.add(rule)
        await db_session.flush()

        response = await client.get("/api/admin/db-stats")

        assert response.status_code == 200
        data = response.json()
        assert data["items_count"] == 5
        assert data["sources_count"] == 1
        assert data["rules_count"] == 1
        assert data["items_with_summary"] == 2
        assert data["items_without_summary"] == 3


class TestDeleteItemsEndpoints:
    """Tests for admin delete endpoints."""

    @pytest.mark.asyncio
    async def test_delete_all_items(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """DELETE /api/admin/items removes all items."""
        # Verify items exist
        stats = await client.get("/api/admin/db-stats")
        assert stats.json()["items_count"] >= 4

        response = await client.delete("/api/admin/items")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] >= 4
        assert "Deleted" in data["message"]

        # Verify deletion
        stats = await client.get("/api/admin/db-stats")
        assert stats.json()["items_count"] == 0

    @pytest.mark.asyncio
    async def test_delete_old_items(
        self, client: AsyncClient, db_session: AsyncSession, channel_in_db: Channel
    ):
        """DELETE /api/admin/items/old removes items older than X days."""
        # Create old and new items
        old_item = Item(
            channel_id=channel_in_db.id,
            external_id="old-1",
            title="Old Article",
            content="Old content",
            url="https://test.com/old",
            published_at=datetime.utcnow() - timedelta(days=60),
            fetched_at=datetime.utcnow() - timedelta(days=60),
            content_hash="oldhash",
        )
        new_item = Item(
            channel_id=channel_in_db.id,
            external_id="new-1",
            title="New Article",
            content="New content",
            url="https://test.com/new",
            published_at=datetime.utcnow(),
            content_hash="newhash",
        )
        db_session.add_all([old_item, new_item])
        await db_session.flush()

        response = await client.delete("/api/admin/items/old", params={"days": 30})

        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] == 1
        assert "older than 30 days" in data["message"]

    @pytest.mark.asyncio
    async def test_delete_old_items_preserves_starred(
        self, client: AsyncClient, db_session: AsyncSession, channel_in_db: Channel
    ):
        """Starred items are preserved when deleting old items."""
        old_starred_item = Item(
            channel_id=channel_in_db.id,
            external_id="old-starred",
            title="Old Starred",
            content="Content",
            url="https://test.com/starred",
            published_at=datetime.utcnow() - timedelta(days=60),
            fetched_at=datetime.utcnow() - timedelta(days=60),
            content_hash="starredhash",
            is_starred=True,
        )
        db_session.add(old_starred_item)
        await db_session.flush()

        response = await client.delete("/api/admin/items/old", params={"days": 30})

        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] == 0

    @pytest.mark.asyncio
    async def test_delete_old_items_invalid_days(self, client: AsyncClient):
        """Invalid days parameter is rejected."""
        # Days too low
        response = await client.delete("/api/admin/items/old", params={"days": 0})
        assert response.status_code == 422

        # Days too high
        response = await client.delete("/api/admin/items/old", params={"days": 500})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_delete_low_priority_items(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """DELETE /api/admin/items/low-priority removes LOW priority items."""
        response = await client.delete("/api/admin/items/low-priority")

        assert response.status_code == 200
        data = response.json()
        # One item is starred (priority CRITICAL), one is LOW but we have 1 LOW item
        assert "low-priority" in data["message"]

    @pytest.mark.asyncio
    async def test_delete_items_by_source_not_found(self, client: AsyncClient):
        """DELETE /api/admin/items/source/{id} returns 404 for non-existent source."""
        response = await client.delete("/api/admin/items/source/99999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestLogsEndpoint:
    """Tests for /api/admin/logs endpoint."""

    @pytest.mark.asyncio
    async def test_get_logs(self, client: AsyncClient):
        """GET /api/admin/logs returns log information."""
        response = await client.get("/api/admin/logs")

        assert response.status_code == 200
        data = response.json()
        assert "lines" in data
        assert "source" in data
        assert "total_lines" in data
        assert isinstance(data["lines"], list)

    @pytest.mark.asyncio
    async def test_get_logs_with_limit(self, client: AsyncClient):
        """GET /api/admin/logs respects lines parameter."""
        response = await client.get("/api/admin/logs", params={"lines": 10})

        assert response.status_code == 200
        data = response.json()
        assert data["total_lines"] <= 10

    @pytest.mark.asyncio
    async def test_get_logs_invalid_limit(self, client: AsyncClient):
        """Invalid lines parameter is rejected."""
        # Lines too low
        response = await client.get("/api/admin/logs", params={"lines": 0})
        assert response.status_code == 422

        # Lines too high
        response = await client.get("/api/admin/logs", params={"lines": 2000})
        assert response.status_code == 422
