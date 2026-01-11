"""Tests for management API endpoints (scheduler, admin, sources operations)."""

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, ConnectorType, Item, Priority, Source, Setting


class TestSchedulerAPI:
    """Tests for /api/scheduler endpoints."""

    @pytest.mark.asyncio
    async def test_get_scheduler_status(self, client: AsyncClient):
        """Test getting scheduler status."""
        response = await client.get("/api/scheduler/status")

        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert "jobs" in data
        assert "fetch_interval_minutes" in data

    @pytest.mark.asyncio
    async def test_start_scheduler_already_running(self, client: AsyncClient):
        """Test starting scheduler when already running."""
        with patch("api.scheduler.scheduler") as mock_scheduler:
            mock_scheduler.running = True

            response = await client.post("/api/scheduler/start")

            assert response.status_code == 200
            assert response.json()["status"] == "already_running"

    @pytest.mark.asyncio
    async def test_stop_scheduler_not_running(self, client: AsyncClient):
        """Test stopping scheduler when not running."""
        with patch("api.scheduler.scheduler") as mock_scheduler:
            mock_scheduler.running = False

            response = await client.post("/api/scheduler/stop")

            assert response.status_code == 200
            assert response.json()["status"] == "already_stopped"


class TestAdminItemCleanupAPI:
    """Tests for /api/admin/items cleanup endpoints."""

    @pytest.mark.asyncio
    async def test_delete_old_items(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting items older than X days."""
        # Create source and channel
        source = Source(name="Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed"},
            source_identifier="https://example.com/feed",
        )
        db_session.add(channel)
        await db_session.flush()

        # Create old and new items
        old_item = Item(
            channel_id=channel.id,
            external_id="old-1",
            title="Old Article",
            content="Content",
            url="https://example.com/old",
            published_at=datetime.utcnow() - timedelta(days=40),
            fetched_at=datetime.utcnow() - timedelta(days=40),
            content_hash="hash_old",
        )
        new_item = Item(
            channel_id=channel.id,
            external_id="new-1",
            title="New Article",
            content="Content",
            url="https://example.com/new",
            published_at=datetime.utcnow(),
            fetched_at=datetime.utcnow(),
            content_hash="hash_new",
        )
        db_session.add(old_item)
        db_session.add(new_item)
        await db_session.flush()

        response = await client.delete("/api/admin/items/old", params={"days": 30})

        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] == 1
        assert "30 days" in data["message"]

    @pytest.mark.asyncio
    async def test_delete_old_items_preserves_starred(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that starred items are not deleted."""
        source = Source(name="Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed"},
            source_identifier="https://example.com/feed",
        )
        db_session.add(channel)
        await db_session.flush()

        starred_old_item = Item(
            channel_id=channel.id,
            external_id="starred-1",
            title="Starred Old Article",
            content="Content",
            url="https://example.com/starred",
            published_at=datetime.utcnow() - timedelta(days=40),
            fetched_at=datetime.utcnow() - timedelta(days=40),
            content_hash="hash_starred",
            is_starred=True,
        )
        db_session.add(starred_old_item)
        await db_session.flush()

        response = await client.delete("/api/admin/items/old", params={"days": 30})

        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] == 0

    @pytest.mark.asyncio
    async def test_delete_items_by_source(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting items by source."""
        # Create two sources with channels
        source1 = Source(name="Source1")
        source2 = Source(name="Source2")
        db_session.add(source1)
        db_session.add(source2)
        await db_session.flush()

        channel1 = Channel(
            source_id=source1.id,
            name="Channel1",
            connector_type=ConnectorType.RSS,
            config={"url": "https://s1.com/feed"},
            source_identifier="https://s1.com/feed",
        )
        channel2 = Channel(
            source_id=source2.id,
            name="Channel2",
            connector_type=ConnectorType.RSS,
            config={"url": "https://s2.com/feed"},
            source_identifier="https://s2.com/feed",
        )
        db_session.add(channel1)
        db_session.add(channel2)
        await db_session.flush()

        # Create items for each channel
        for i in range(3):
            db_session.add(Item(
                channel_id=channel1.id,
                external_id=f"s1-{i}",
                title=f"Article {i}",
                content="Content",
                url=f"https://example.com/s1/{i}",
                published_at=datetime.utcnow(),
                content_hash=f"hash_s1_{i}",
            ))
        for i in range(2):
            db_session.add(Item(
                channel_id=channel2.id,
                external_id=f"s2-{i}",
                title=f"Article {i}",
                content="Content",
                url=f"https://example.com/s2/{i}",
                published_at=datetime.utcnow(),
                content_hash=f"hash_s2_{i}",
            ))
        await db_session.flush()

        response = await client.delete(f"/api/admin/items/source/{source1.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] == 3
        assert "Source1" in data["message"]

    @pytest.mark.asyncio
    async def test_delete_items_by_source_not_found(self, client: AsyncClient):
        """Test deleting items for non-existent source returns 404."""
        response = await client.delete("/api/admin/items/source/99999")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_low_priority_items(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting low priority items."""
        source = Source(name="Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed"},
            source_identifier="https://example.com/feed",
        )
        db_session.add(channel)
        await db_session.flush()

        # Create items with different priorities
        for i, priority in enumerate([Priority.LOW, Priority.LOW, Priority.MEDIUM, Priority.HIGH]):
            db_session.add(Item(
                channel_id=channel.id,
                external_id=f"p-{i}-{priority.value}",
                title=f"Article {priority.value}",
                content="Content",
                url=f"https://example.com/{i}/{priority.value}",
                published_at=datetime.utcnow(),
                content_hash=f"hash_{i}_{priority.value}",
                priority=priority,
            ))
        await db_session.flush()

        response = await client.delete("/api/admin/items/low-priority")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] == 2


class TestAdminHealthAPI:
    """Tests for /api/admin/health endpoint."""

    @pytest.mark.asyncio
    async def test_get_system_health(self, client: AsyncClient):
        """Test getting comprehensive system health."""
        response = await client.get("/api/admin/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "scheduler_running" in data
        assert "llm_available" in data
        assert "proxy_count" in data
        assert "database_ok" in data
        assert "items_count" in data
        assert "sources_count" in data


class TestAdminLogsAPI:
    """Tests for /api/admin/logs endpoint."""

    @pytest.mark.asyncio
    async def test_get_logs(self, client: AsyncClient):
        """Test getting application logs."""
        response = await client.get("/api/admin/logs", params={"lines": 50})

        assert response.status_code == 200
        data = response.json()
        assert "lines" in data
        assert "source" in data
        assert "total_lines" in data


class TestSourcesOperationsAPI:
    """Tests for source enable/disable/errors endpoints."""

    @pytest.mark.asyncio
    async def test_enable_source(
        self, client: AsyncClient, sample_source_data: dict[str, Any]
    ):
        """Test enabling a source."""
        # Create disabled source
        disabled_data = {**sample_source_data, "enabled": False}
        create_response = await client.post("/api/sources", json=disabled_data)
        source_id = create_response.json()["id"]

        # Enable source
        response = await client.post(f"/api/sources/{source_id}/enable")

        assert response.status_code == 200
        assert response.json()["enabled"] is True

    @pytest.mark.asyncio
    async def test_disable_source(
        self, client: AsyncClient, sample_source_data: dict[str, Any]
    ):
        """Test disabling a source."""
        # Create enabled source
        create_response = await client.post("/api/sources", json=sample_source_data)
        source_id = create_response.json()["id"]

        # Disable source
        response = await client.post(f"/api/sources/{source_id}/disable")

        assert response.status_code == 200
        assert response.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_enable_source_not_found(self, client: AsyncClient):
        """Test enabling non-existent source returns 404."""
        response = await client.post("/api/sources/99999/enable")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_disable_source_not_found(self, client: AsyncClient):
        """Test disabling non-existent source returns 404."""
        response = await client.post("/api/sources/99999/disable")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_sources_with_errors(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test listing sources with errors."""
        # Create sources
        source_ok = Source(name="OK Source")
        source_error = Source(name="Error Source")
        db_session.add(source_ok)
        db_session.add(source_error)
        await db_session.flush()

        # Create channels - errors are on channels, not sources
        channel_ok = Channel(
            source_id=source_ok.id,
            name="OK Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://ok.com/feed"},
            source_identifier="https://ok.com/feed",
            last_error=None,
        )
        channel_error = Channel(
            source_id=source_error.id,
            name="Error Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://error.com/feed"},
            source_identifier="https://error.com/feed",
            last_error="Connection timeout",
        )
        db_session.add(channel_ok)
        db_session.add(channel_error)
        await db_session.flush()

        response = await client.get("/api/sources/errors")

        assert response.status_code == 200
        data = response.json()
        # Response should contain sources that have channels with errors
        assert len(data) >= 1
        error_source = next((s for s in data if s["name"] == "Error Source"), None)
        assert error_source is not None


class TestLLMModelAPI:
    """Tests for /api/llm/model endpoints."""

    @pytest.mark.asyncio
    async def test_get_selected_model_default(self, client: AsyncClient):
        """Test getting selected model returns config default."""
        response = await client.get("/api/llm/model")

        assert response.status_code == 200
        data = response.json()
        assert "model" in data
        assert "source" in data

    @pytest.mark.asyncio
    async def test_select_model_ollama_unavailable(self, client: AsyncClient):
        """Test selecting model when Ollama is unavailable."""
        with patch("api.llm.OllamaProvider") as mock_provider_class:
            mock_provider = MagicMock()
            mock_provider.is_available = AsyncMock(return_value=False)
            mock_provider_class.return_value = mock_provider

            response = await client.put(
                "/api/llm/model",
                json={"model": "llama3:8b"},
            )

            assert response.status_code == 503
            assert "not available" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_select_model_not_found(self, client: AsyncClient):
        """Test selecting non-existent model."""
        with patch("api.llm.OllamaProvider") as mock_provider_class:
            mock_provider = MagicMock()
            mock_provider.is_available = AsyncMock(return_value=True)
            mock_provider.list_models = AsyncMock(return_value=["model1", "model2"])
            mock_provider_class.return_value = mock_provider

            response = await client.put(
                "/api/llm/model",
                json={"model": "nonexistent-model"},
            )

            assert response.status_code == 400
            assert "not found" in response.json()["detail"]
