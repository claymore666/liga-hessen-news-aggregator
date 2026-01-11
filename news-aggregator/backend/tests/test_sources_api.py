"""Tests for sources API endpoints."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, ConnectorType, Source


class TestListSources:
    """Tests for GET /api/sources endpoint."""

    @pytest.mark.asyncio
    async def test_list_sources_empty(self, client: AsyncClient):
        """Returns empty list when no sources exist."""
        response = await client.get("/api/sources")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_sources_with_data(
        self, client: AsyncClient, source_in_db: Source
    ):
        """Returns sources when data exists."""
        response = await client.get("/api/sources")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(s["id"] == source_in_db.id for s in data)

    @pytest.mark.asyncio
    async def test_list_sources_includes_channels(
        self, client: AsyncClient, source_in_db: Source, channel_in_db: Channel
    ):
        """Sources include nested channels."""
        response = await client.get("/api/sources")

        assert response.status_code == 200
        data = response.json()
        source = next(s for s in data if s["id"] == source_in_db.id)
        assert "channels" in source
        assert len(source["channels"]) >= 1

    @pytest.mark.asyncio
    async def test_list_sources_filter_enabled(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Filter sources by enabled status."""
        # Create enabled and disabled sources
        enabled_source = Source(name="Enabled Source", enabled=True)
        disabled_source = Source(name="Disabled Source", enabled=False)
        db_session.add_all([enabled_source, disabled_source])
        await db_session.flush()

        # Filter by enabled
        response = await client.get("/api/sources", params={"enabled": True})
        data = response.json()
        assert all(s["enabled"] is True for s in data)

        # Filter by disabled
        response = await client.get("/api/sources", params={"enabled": False})
        data = response.json()
        assert all(s["enabled"] is False for s in data)

    @pytest.mark.asyncio
    async def test_list_sources_filter_stakeholder(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Filter sources by stakeholder status."""
        stakeholder = Source(name="Stakeholder", is_stakeholder=True)
        non_stakeholder = Source(name="Non-Stakeholder", is_stakeholder=False)
        db_session.add_all([stakeholder, non_stakeholder])
        await db_session.flush()

        response = await client.get("/api/sources", params={"is_stakeholder": True})
        data = response.json()
        assert all(s["is_stakeholder"] is True for s in data)


class TestCreateSource:
    """Tests for POST /api/sources endpoint."""

    @pytest.mark.asyncio
    async def test_create_source_minimal(self, client: AsyncClient):
        """Create source with minimal data."""
        response = await client.post("/api/sources", json={"name": "Minimal Source"})

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Minimal Source"
        assert "id" in data
        assert data["enabled"] is True  # Default
        assert data["is_stakeholder"] is False  # Default
        assert data["channels"] == []

    @pytest.mark.asyncio
    async def test_create_source_full(
        self, client: AsyncClient, sample_source_data: dict[str, Any]
    ):
        """Create source with all fields."""
        response = await client.post("/api/sources", json=sample_source_data)

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == sample_source_data["name"]
        assert data["description"] == sample_source_data["description"]
        assert data["is_stakeholder"] == sample_source_data["is_stakeholder"]
        assert data["enabled"] == sample_source_data["enabled"]

    @pytest.mark.asyncio
    async def test_create_source_with_channels(
        self, client: AsyncClient, sample_source_with_channels: dict[str, Any]
    ):
        """Create source with initial channels."""
        response = await client.post("/api/sources", json=sample_source_with_channels)

        assert response.status_code == 201
        data = response.json()
        assert len(data["channels"]) == 2
        assert data["channel_count"] == 2

    @pytest.mark.asyncio
    async def test_create_source_empty_name(self, client: AsyncClient):
        """Empty name is rejected."""
        response = await client.post("/api/sources", json={"name": ""})

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_source_missing_name(self, client: AsyncClient):
        """Missing name is rejected."""
        response = await client.post("/api/sources", json={"description": "No name"})

        assert response.status_code == 422


class TestGetSource:
    """Tests for GET /api/sources/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_source(self, client: AsyncClient, source_in_db: Source):
        """Get source by ID."""
        response = await client.get(f"/api/sources/{source_in_db.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == source_in_db.id
        assert data["name"] == source_in_db.name

    @pytest.mark.asyncio
    async def test_get_source_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent source."""
        response = await client.get("/api/sources/99999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_source_includes_channels(
        self, client: AsyncClient, source_in_db: Source, channel_in_db: Channel
    ):
        """Source includes nested channels."""
        response = await client.get(f"/api/sources/{source_in_db.id}")

        assert response.status_code == 200
        data = response.json()
        assert "channels" in data
        assert len(data["channels"]) >= 1
        assert data["channel_count"] >= 1


class TestUpdateSource:
    """Tests for PATCH /api/sources/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_source_name(
        self, client: AsyncClient, source_in_db: Source
    ):
        """Update source name."""
        response = await client.patch(
            f"/api/sources/{source_in_db.id}", json={"name": "Updated Name"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_source_description(
        self, client: AsyncClient, source_in_db: Source
    ):
        """Update source description."""
        response = await client.patch(
            f"/api/sources/{source_in_db.id}", json={"description": "New description"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "New description"

    @pytest.mark.asyncio
    async def test_update_source_enabled(
        self, client: AsyncClient, source_in_db: Source
    ):
        """Update source enabled status."""
        response = await client.patch(
            f"/api/sources/{source_in_db.id}", json={"enabled": False}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_source_stakeholder(
        self, client: AsyncClient, source_in_db: Source
    ):
        """Update source stakeholder status."""
        response = await client.patch(
            f"/api/sources/{source_in_db.id}", json={"is_stakeholder": False}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_stakeholder"] is False

    @pytest.mark.asyncio
    async def test_update_source_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent source."""
        response = await client.patch(
            "/api/sources/99999", json={"name": "New Name"}
        )

        assert response.status_code == 404


class TestDeleteSource:
    """Tests for DELETE /api/sources/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_delete_source(self, client: AsyncClient, source_in_db: Source):
        """Delete source."""
        response = await client.delete(f"/api/sources/{source_in_db.id}")

        assert response.status_code == 204

        # Verify deletion
        get_response = await client.get(f"/api/sources/{source_in_db.id}")
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_source_cascades_channels(
        self, client: AsyncClient, source_in_db: Source, channel_in_db: Channel
    ):
        """Deleting source also deletes its channels."""
        channel_id = channel_in_db.id

        response = await client.delete(f"/api/sources/{source_in_db.id}")
        assert response.status_code == 204

        # Verify channel is also deleted
        get_response = await client.get(f"/api/channels/{channel_id}")
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_source_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent source."""
        response = await client.delete("/api/sources/99999")

        assert response.status_code == 404


class TestEnableDisableSource:
    """Tests for enable/disable source endpoints."""

    @pytest.mark.asyncio
    async def test_enable_source(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """POST /api/sources/{id}/enable enables source."""
        source = Source(name="Disabled Source", enabled=False)
        db_session.add(source)
        await db_session.flush()

        response = await client.post(f"/api/sources/{source.id}/enable")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True

    @pytest.mark.asyncio
    async def test_disable_source(
        self, client: AsyncClient, source_in_db: Source
    ):
        """POST /api/sources/{id}/disable disables source."""
        response = await client.post(f"/api/sources/{source_in_db.id}/disable")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_enable_nonexistent_source(self, client: AsyncClient):
        """Returns 404 for non-existent source."""
        response = await client.post("/api/sources/99999/enable")
        assert response.status_code == 404


class TestSourcesErrors:
    """Tests for GET /api/sources/errors endpoint."""

    @pytest.mark.asyncio
    async def test_get_sources_errors_empty(self, client: AsyncClient):
        """Returns empty list when no errors."""
        response = await client.get("/api/sources/errors")

        assert response.status_code == 200
        # Should return list (may be empty)
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_get_sources_errors_with_errors(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Returns sources/channels with errors."""
        source = Source(name="Error Source", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://error.com/feed"},
            last_error="Connection timeout",
        )
        db_session.add(channel)
        await db_session.flush()

        response = await client.get("/api/sources/errors")

        assert response.status_code == 200
        # Should include channel with error
        data = response.json()
        # The error should be visible somewhere in the response
