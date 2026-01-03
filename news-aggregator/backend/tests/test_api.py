"""Tests for API endpoints."""

from datetime import datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import ConnectorType, Item, Priority, Rule, RuleType, Source


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        """Test health check returns healthy status."""
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


class TestSourcesAPI:
    """Tests for /api/sources endpoints."""

    @pytest.mark.asyncio
    async def test_list_sources_empty(self, client: AsyncClient):
        """Test listing sources when empty."""
        response = await client.get("/api/sources")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_create_source(
        self, client: AsyncClient, sample_source_data: dict[str, Any]
    ):
        """Test creating a source."""
        response = await client.post("/api/sources", json=sample_source_data)

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == sample_source_data["name"]
        assert data["connector_type"] == sample_source_data["connector_type"]
        assert data["id"] is not None

    @pytest.mark.asyncio
    async def test_get_source(
        self, client: AsyncClient, sample_source_data: dict[str, Any]
    ):
        """Test getting a single source."""
        # Create source
        create_response = await client.post("/api/sources", json=sample_source_data)
        source_id = create_response.json()["id"]

        # Get source
        response = await client.get(f"/api/sources/{source_id}")

        assert response.status_code == 200
        assert response.json()["id"] == source_id

    @pytest.mark.asyncio
    async def test_get_source_not_found(self, client: AsyncClient):
        """Test getting non-existent source returns 404."""
        response = await client.get("/api/sources/99999")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_source(
        self, client: AsyncClient, sample_source_data: dict[str, Any]
    ):
        """Test updating a source."""
        # Create source
        create_response = await client.post("/api/sources", json=sample_source_data)
        source_id = create_response.json()["id"]

        # Update source
        response = await client.patch(
            f"/api/sources/{source_id}",
            json={"name": "Updated Name", "enabled": False},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_delete_source(
        self, client: AsyncClient, sample_source_data: dict[str, Any]
    ):
        """Test deleting a source."""
        # Create source
        create_response = await client.post("/api/sources", json=sample_source_data)
        source_id = create_response.json()["id"]

        # Delete source
        response = await client.delete(f"/api/sources/{source_id}")
        assert response.status_code == 204

        # Verify deleted
        get_response = await client.get(f"/api/sources/{source_id}")
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_sources_filtered(
        self, client: AsyncClient, sample_source_data: dict[str, Any]
    ):
        """Test filtering sources by enabled status."""
        # Create enabled and disabled sources
        await client.post("/api/sources", json=sample_source_data)
        disabled_data = {**sample_source_data, "name": "Disabled", "enabled": False}
        await client.post("/api/sources", json=disabled_data)

        # Filter by enabled
        response = await client.get("/api/sources", params={"enabled": True})
        assert len(response.json()) == 1
        assert response.json()[0]["enabled"] is True


class TestRulesAPI:
    """Tests for /api/rules endpoints."""

    @pytest.mark.asyncio
    async def test_list_rules_empty(self, client: AsyncClient):
        """Test listing rules when empty."""
        response = await client.get("/api/rules")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_create_rule(
        self, client: AsyncClient, sample_rule_data: dict[str, Any]
    ):
        """Test creating a rule."""
        response = await client.post("/api/rules", json=sample_rule_data)

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == sample_rule_data["name"]
        assert data["rule_type"] == sample_rule_data["rule_type"]

    @pytest.mark.asyncio
    async def test_update_rule(
        self, client: AsyncClient, sample_rule_data: dict[str, Any]
    ):
        """Test updating a rule."""
        # Create rule
        create_response = await client.post("/api/rules", json=sample_rule_data)
        rule_id = create_response.json()["id"]

        # Update rule
        response = await client.patch(
            f"/api/rules/{rule_id}",
            json={"priority_boost": 50, "enabled": False},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["priority_boost"] == 50
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_delete_rule(
        self, client: AsyncClient, sample_rule_data: dict[str, Any]
    ):
        """Test deleting a rule."""
        # Create rule
        create_response = await client.post("/api/rules", json=sample_rule_data)
        rule_id = create_response.json()["id"]

        # Delete rule
        response = await client.delete(f"/api/rules/{rule_id}")
        assert response.status_code == 204


class TestItemsAPI:
    """Tests for /api/items endpoints."""

    @pytest.mark.asyncio
    async def test_list_items_empty(self, client: AsyncClient):
        """Test listing items when empty."""
        response = await client.get("/api/items")

        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_items_with_data(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test listing items with data."""
        # Create source and items directly in DB
        source = Source(
            name="Test",
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(source)
        await db_session.flush()

        for i in range(3):
            item = Item(
                source_id=source.id,
                external_id=f"ext-{i}",
                title=f"Article {i}",
                content=f"Content {i}",
                url=f"https://example.com/{i}",
                published_at=datetime.utcnow(),
                content_hash=f"hash{i}",
            )
            db_session.add(item)
        await db_session.flush()

        response = await client.get("/api/items")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    @pytest.mark.asyncio
    async def test_get_item(self, client: AsyncClient, db_session: AsyncSession):
        """Test getting a single item."""
        source = Source(
            name="Test",
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(source)
        await db_session.flush()

        item = Item(
            source_id=source.id,
            external_id="ext-1",
            title="Test Article",
            content="Content",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash1",
        )
        db_session.add(item)
        await db_session.flush()

        response = await client.get(f"/api/items/{item.id}")

        assert response.status_code == 200
        assert response.json()["title"] == "Test Article"

    @pytest.mark.asyncio
    async def test_update_item(self, client: AsyncClient, db_session: AsyncSession):
        """Test updating an item."""
        source = Source(
            name="Test",
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(source)
        await db_session.flush()

        item = Item(
            source_id=source.id,
            external_id="ext-1",
            title="Test Article",
            content="Content",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash1",
        )
        db_session.add(item)
        await db_session.flush()

        response = await client.patch(
            f"/api/items/{item.id}",
            json={"is_read": True, "is_starred": True, "notes": "Important!"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_read"] is True
        assert data["is_starred"] is True
        assert data["notes"] == "Important!"

    @pytest.mark.asyncio
    async def test_items_pagination(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test items pagination."""
        source = Source(
            name="Test",
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(source)
        await db_session.flush()

        for i in range(25):
            item = Item(
                source_id=source.id,
                external_id=f"ext-{i}",
                title=f"Article {i}",
                content=f"Content {i}",
                url=f"https://example.com/{i}",
                published_at=datetime.utcnow(),
                content_hash=f"hash{i}",
            )
            db_session.add(item)
        await db_session.flush()

        # First page
        response = await client.get("/api/items", params={"page": 1, "page_size": 10})
        data = response.json()
        assert data["total"] == 25
        assert len(data["items"]) == 10
        assert data["page"] == 1
        assert data["total_pages"] == 3

        # Second page
        response = await client.get("/api/items", params={"page": 2, "page_size": 10})
        data = response.json()
        assert len(data["items"]) == 10
        assert data["page"] == 2


class TestConnectorsAPI:
    """Tests for /api/connectors endpoints."""

    @pytest.mark.asyncio
    async def test_list_connectors(self, client: AsyncClient):
        """Test listing available connectors."""
        response = await client.get("/api/connectors")

        assert response.status_code == 200
        connectors = response.json()

        # Should have all connector types
        connector_types = [c["type"] for c in connectors]
        assert "rss" in connector_types
        assert "html" in connector_types
        assert "bluesky" in connector_types
        assert "twitter" in connector_types
        assert "mastodon" in connector_types

    @pytest.mark.asyncio
    async def test_get_connector(self, client: AsyncClient):
        """Test getting a specific connector."""
        response = await client.get("/api/connectors/rss")

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "rss"
        assert "config_schema" in data


class TestStatsAPI:
    """Tests for /api/stats endpoints."""

    @pytest.mark.asyncio
    async def test_get_stats_empty(self, client: AsyncClient):
        """Test getting stats when empty."""
        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["total_items"] == 0
        assert data["sources_count"] == 0

    @pytest.mark.asyncio
    async def test_get_stats_with_data(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test getting stats with data."""
        # Create sources
        for i in range(3):
            source = Source(
                name=f"Source {i}",
                connector_type=ConnectorType.RSS,
                config={},
                enabled=i < 2,  # 2 enabled, 1 disabled
            )
            db_session.add(source)
        await db_session.flush()

        # Create items
        sources = (await db_session.execute(
            __import__("sqlalchemy").select(Source)
        )).scalars().all()

        for source in sources:
            for j in range(2):
                item = Item(
                    source_id=source.id,
                    external_id=f"ext-{source.id}-{j}",
                    title=f"Article {j}",
                    content="Content",
                    url=f"https://example.com/{source.id}/{j}",
                    published_at=datetime.utcnow(),
                    content_hash=f"hash{source.id}{j}",
                    is_read=j == 0,
                    priority=Priority.CRITICAL if j == 0 else Priority.LOW,
                )
                db_session.add(item)
        await db_session.flush()

        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["total_items"] == 6
        assert data["sources_count"] == 3
        assert data["enabled_sources"] == 2
        assert data["unread_items"] == 3
        assert data["critical_items"] == 3
