"""Tests for items API endpoints."""

from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, ConnectorType, Item, Priority, Source


class TestListItems:
    """Tests for GET /api/items endpoint."""

    @pytest.mark.asyncio
    async def test_list_items_empty(self, client: AsyncClient):
        """Returns empty list when no items exist."""
        response = await client.get("/api/items")

        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["total_pages"] == 0

    @pytest.mark.asyncio
    async def test_list_items_with_data(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Returns items when data exists."""
        response = await client.get("/api/items", params={"relevant_only": False})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 4
        assert len(data["items"]) >= 4

    @pytest.mark.asyncio
    async def test_list_items_relevant_only_default(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """By default, excludes NONE priority items (not Liga-relevant)."""
        response = await client.get("/api/items")

        assert response.status_code == 200
        data = response.json()
        # Should exclude NONE priority items (only HIGH, MEDIUM, LOW are shown)
        for item in data["items"]:
            assert item["priority"] != "none"

    @pytest.mark.asyncio
    async def test_list_items_relevant_only_false(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Can include NONE priority items when relevant_only=false."""
        response = await client.get("/api/items", params={"relevant_only": False})

        assert response.status_code == 200
        data = response.json()
        priorities = [item["priority"] for item in data["items"]]
        # Should include all priorities including NONE
        assert "none" in priorities

    @pytest.mark.asyncio
    async def test_list_items_pagination(
        self, client: AsyncClient, db_session: AsyncSession, channel_in_db: Channel
    ):
        """Pagination works correctly."""
        # Create 25 items
        for i in range(25):
            item = Item(
                channel_id=channel_in_db.id,
                external_id=f"page-test-{i}",
                title=f"Page Test {i}",
                content="Content",
                url=f"https://test.com/page/{i}",
                published_at=datetime.utcnow() - timedelta(hours=i),
                content_hash=f"pagehash{i}",
                priority=Priority.MEDIUM,
            )
            db_session.add(item)
        await db_session.flush()

        # First page
        response = await client.get(
            "/api/items", params={"page": 1, "page_size": 10, "relevant_only": False}
        )
        data = response.json()
        assert data["page"] == 1
        assert data["page_size"] == 10
        assert len(data["items"]) == 10
        assert data["total_pages"] == 3

        # Second page
        response = await client.get(
            "/api/items", params={"page": 2, "page_size": 10, "relevant_only": False}
        )
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) == 10

        # Third page (partial)
        response = await client.get(
            "/api/items", params={"page": 3, "page_size": 10, "relevant_only": False}
        )
        data = response.json()
        assert data["page"] == 3
        assert len(data["items"]) == 5

    @pytest.mark.asyncio
    async def test_list_items_filter_by_priority(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Filter by specific priority."""
        response = await client.get("/api/items", params={"priority": "high"})

        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["priority"] == "high"

    @pytest.mark.asyncio
    async def test_list_items_filter_by_read_status(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Filter by read status."""
        # Unread items
        response = await client.get(
            "/api/items", params={"is_read": False, "relevant_only": False}
        )
        assert response.status_code == 200
        for item in response.json()["items"]:
            assert item["is_read"] is False

        # Read items
        response = await client.get(
            "/api/items", params={"is_read": True, "relevant_only": False}
        )
        assert response.status_code == 200
        for item in response.json()["items"]:
            assert item["is_read"] is True

    @pytest.mark.asyncio
    async def test_list_items_filter_by_starred(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Filter by starred status."""
        response = await client.get(
            "/api/items", params={"is_starred": True, "relevant_only": False}
        )

        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["is_starred"] is True

    @pytest.mark.asyncio
    async def test_list_items_filter_by_date_range(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Filter by date range."""
        since = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        response = await client.get(
            "/api/items", params={"since": since, "relevant_only": False}
        )

        assert response.status_code == 200
        # Should only include recent items

    @pytest.mark.asyncio
    async def test_list_items_search(
        self, client: AsyncClient, db_session: AsyncSession, channel_in_db: Channel
    ):
        """Search in title and content."""
        item = Item(
            channel_id=channel_in_db.id,
            external_id="search-test",
            title="Unique Search Term Xyz123",
            content="Some content with keyword Pflege",
            url="https://test.com/search",
            published_at=datetime.utcnow(),
            content_hash="searchhash",
            priority=Priority.MEDIUM,
        )
        db_session.add(item)
        await db_session.flush()

        # Search in title
        response = await client.get("/api/items", params={"search": "Xyz123"})
        assert response.status_code == 200
        assert len(response.json()["items"]) >= 1

        # Search in content
        response = await client.get("/api/items", params={"search": "Pflege"})
        assert response.status_code == 200
        assert len(response.json()["items"]) >= 1

    @pytest.mark.asyncio
    async def test_list_items_sort_by_date(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Sort by date (default)."""
        response = await client.get(
            "/api/items", params={"sort_by": "date", "relevant_only": False}
        )

        assert response.status_code == 200
        items = response.json()["items"]
        if len(items) > 1:
            dates = [item["published_at"] for item in items]
            assert dates == sorted(dates, reverse=True)

    @pytest.mark.asyncio
    async def test_list_items_sort_by_priority(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Sort by priority."""
        response = await client.get(
            "/api/items", params={"sort_by": "priority", "relevant_only": False}
        )

        assert response.status_code == 200
        items = response.json()["items"]
        if len(items) > 1:
            priority_order = {"high": 1, "medium": 2, "low": 3, "none": 4}
            priorities = [priority_order.get(item["priority"], 5) for item in items]
            assert priorities == sorted(priorities)

    @pytest.mark.asyncio
    async def test_list_items_invalid_page_size(self, client: AsyncClient):
        """Invalid page_size is rejected."""
        response = await client.get("/api/items", params={"page_size": 200})
        assert response.status_code == 422

        response = await client.get("/api/items", params={"page_size": 0})
        assert response.status_code == 422


class TestGetItem:
    """Tests for GET /api/items/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_item(self, client: AsyncClient, item_in_db: Item):
        """Get single item by ID."""
        response = await client.get(f"/api/items/{item_in_db.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == item_in_db.id
        assert data["title"] == item_in_db.title
        assert data["content"] == item_in_db.content

    @pytest.mark.asyncio
    async def test_get_item_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent item."""
        response = await client.get("/api/items/99999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_item_includes_channel(
        self, client: AsyncClient, item_in_db: Item
    ):
        """Item response includes channel information."""
        response = await client.get(f"/api/items/{item_in_db.id}")

        assert response.status_code == 200
        data = response.json()
        assert "channel" in data
        assert data["channel"] is not None
        assert "connector_type" in data["channel"]

    @pytest.mark.asyncio
    async def test_get_item_includes_source(
        self, client: AsyncClient, item_in_db: Item
    ):
        """Item response includes source information."""
        response = await client.get(f"/api/items/{item_in_db.id}")

        assert response.status_code == 200
        data = response.json()
        assert "source" in data
        assert data["source"] is not None
        assert "name" in data["source"]


class TestUpdateItem:
    """Tests for PATCH /api/items/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_item_read_status(
        self, client: AsyncClient, item_in_db: Item
    ):
        """Update item read status."""
        response = await client.patch(
            f"/api/items/{item_in_db.id}", json={"is_read": True}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_read"] is True

    @pytest.mark.asyncio
    async def test_update_item_starred(self, client: AsyncClient, item_in_db: Item):
        """Update item starred status."""
        response = await client.patch(
            f"/api/items/{item_in_db.id}", json={"is_starred": True}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_starred"] is True

    @pytest.mark.asyncio
    async def test_update_item_notes(self, client: AsyncClient, item_in_db: Item):
        """Update item notes."""
        response = await client.patch(
            f"/api/items/{item_in_db.id}", json={"notes": "Important for AK3"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["notes"] == "Important for AK3"

    @pytest.mark.asyncio
    async def test_update_item_content(self, client: AsyncClient, item_in_db: Item):
        """Update item content (admin correction)."""
        new_content = "Updated content for testing"
        response = await client.patch(
            f"/api/items/{item_in_db.id}", json={"content": new_content}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["content"] == new_content

    @pytest.mark.asyncio
    async def test_update_item_summary(self, client: AsyncClient, item_in_db: Item):
        """Update item summary."""
        new_summary = "Manual summary override"
        response = await client.patch(
            f"/api/items/{item_in_db.id}", json={"summary": new_summary}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["summary"] == new_summary

    @pytest.mark.asyncio
    async def test_update_item_priority(self, client: AsyncClient, item_in_db: Item):
        """Update item priority."""
        response = await client.patch(
            f"/api/items/{item_in_db.id}", json={"priority": "high"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["priority"] == "high"

    @pytest.mark.asyncio
    async def test_update_item_invalid_priority(
        self, client: AsyncClient, item_in_db: Item
    ):
        """Invalid priority value is rejected."""
        response = await client.patch(
            f"/api/items/{item_in_db.id}", json={"priority": "invalid"}
        )

        assert response.status_code == 400
        assert "invalid priority" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_update_item_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent item."""
        response = await client.patch("/api/items/99999", json={"is_read": True})

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_multiple_fields(self, client: AsyncClient, item_in_db: Item):
        """Update multiple fields at once."""
        response = await client.patch(
            f"/api/items/{item_in_db.id}",
            json={
                "is_read": True,
                "is_starred": True,
                "notes": "Updated notes",
                "priority": "high",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_read"] is True
        assert data["is_starred"] is True
        assert data["notes"] == "Updated notes"
        assert data["priority"] == "high"


class TestMarkAsRead:
    """Tests for POST /api/items/{id}/read endpoint."""

    @pytest.mark.asyncio
    async def test_mark_as_read(self, client: AsyncClient, item_in_db: Item):
        """Mark single item as read."""
        response = await client.post(f"/api/items/{item_in_db.id}/read")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify item is now read
        get_response = await client.get(f"/api/items/{item_in_db.id}")
        assert get_response.json()["is_read"] is True

    @pytest.mark.asyncio
    async def test_mark_as_read_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent item."""
        response = await client.post("/api/items/99999/read")

        assert response.status_code == 404


class TestMarkAllAsRead:
    """Tests for POST /api/items/mark-all-read endpoint."""

    @pytest.mark.asyncio
    async def test_mark_all_as_read(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Mark all items as read."""
        response = await client.post("/api/items/mark-all-read")

        assert response.status_code == 200
        data = response.json()
        assert "marked" in data

    @pytest.mark.asyncio
    async def test_mark_all_as_read_by_source(
        self, client: AsyncClient, multiple_items_in_db: list[Item], source_in_db: Source
    ):
        """Mark all items from a specific source as read."""
        response = await client.post(
            "/api/items/mark-all-read", params={"source_id": source_in_db.id}
        )

        assert response.status_code == 200
        data = response.json()
        assert "marked" in data


class TestReprocessItem:
    """Tests for POST /api/items/{id}/reprocess endpoint."""

    @pytest.mark.asyncio
    async def test_reprocess_item(self, client: AsyncClient, item_in_db: Item):
        """Trigger reprocessing of single item."""
        response = await client.post(f"/api/items/{item_in_db.id}/reprocess")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["item_id"] == item_in_db.id

    @pytest.mark.asyncio
    async def test_reprocess_item_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent item."""
        response = await client.post("/api/items/99999/reprocess")

        assert response.status_code == 404


class TestBatchReprocess:
    """Tests for POST /api/items/reprocess endpoint."""

    @pytest.mark.asyncio
    async def test_batch_reprocess(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Trigger batch reprocessing."""
        response = await client.post(
            "/api/items/reprocess", params={"limit": 10, "force": True}
        )

        assert response.status_code == 200
        data = response.json()
        assert "status" in data

    @pytest.mark.asyncio
    async def test_batch_reprocess_with_filters(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Batch reprocess with priority filter."""
        response = await client.post(
            "/api/items/reprocess",
            params={"priority": "medium", "limit": 5},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_batch_reprocess_exclude_low(
        self, client: AsyncClient, multiple_items_in_db: list[Item]
    ):
        """Batch reprocess excluding low priority items."""
        response = await client.post(
            "/api/items/reprocess", params={"exclude_low": True, "limit": 10}
        )

        assert response.status_code == 200


class TestRefetchItem:
    """Tests for POST /api/items/{id}/refetch endpoint."""

    @pytest.mark.asyncio
    async def test_refetch_non_social_item(
        self, client: AsyncClient, item_in_db: Item
    ):
        """Refetch rejects non-x_scraper/linkedin items."""
        # item_in_db is from RSS channel
        response = await client.post(f"/api/items/{item_in_db.id}/refetch")

        assert response.status_code == 400
        assert "x_scraper" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_refetch_item_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent item."""
        response = await client.post("/api/items/99999/refetch")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_refetch_x_scraper_item(
        self, client: AsyncClient, db_session: AsyncSession, source_in_db: Source
    ):
        """Refetch accepts x_scraper items."""
        # Create x_scraper channel
        channel = Channel(
            source_id=source_in_db.id,
            connector_type=ConnectorType.X_SCRAPER,
            config={"handle": "@test"},
        )
        db_session.add(channel)
        await db_session.flush()

        item = Item(
            channel_id=channel.id,
            external_id="tweet-123",
            title="Test Tweet",
            content="Tweet content with link",
            url="https://x.com/test/status/123",
            published_at=datetime.utcnow(),
            content_hash="tweethash",
        )
        db_session.add(item)
        await db_session.flush()

        response = await client.post(f"/api/items/{item.id}/refetch")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["connector_type"] == "x_scraper"
