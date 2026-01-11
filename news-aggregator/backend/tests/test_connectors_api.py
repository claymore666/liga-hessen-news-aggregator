"""Tests for connectors API endpoints."""

import pytest
from httpx import AsyncClient


class TestListConnectors:
    """Tests for GET /api/connectors endpoint."""

    @pytest.mark.asyncio
    async def test_list_connectors(self, client: AsyncClient):
        """List all available connectors."""
        response = await client.get("/api/connectors")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        # Check common connector types
        connector_types = [c["type"] for c in data]
        assert "rss" in connector_types
        assert "bluesky" in connector_types

    @pytest.mark.asyncio
    async def test_list_connectors_structure(self, client: AsyncClient):
        """Connectors have expected structure."""
        response = await client.get("/api/connectors")

        assert response.status_code == 200
        data = response.json()

        for connector in data:
            assert "type" in connector
            assert "name" in connector
            assert "description" in connector
            assert "config_schema" in connector


class TestGetConnector:
    """Tests for GET /api/connectors/{type} endpoint."""

    @pytest.mark.asyncio
    async def test_get_rss_connector(self, client: AsyncClient):
        """Get RSS connector details."""
        response = await client.get("/api/connectors/rss")

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "rss"
        assert "name" in data
        assert "config_schema" in data
        # RSS should require url in config
        schema = data["config_schema"]
        assert "url" in str(schema).lower() or "properties" in schema

    @pytest.mark.asyncio
    async def test_get_x_scraper_connector(self, client: AsyncClient):
        """Get X scraper connector details."""
        response = await client.get("/api/connectors/x_scraper")

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "x_scraper"

    @pytest.mark.asyncio
    async def test_get_bluesky_connector(self, client: AsyncClient):
        """Get Bluesky connector details."""
        response = await client.get("/api/connectors/bluesky")

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "bluesky"

    @pytest.mark.asyncio
    async def test_get_mastodon_connector(self, client: AsyncClient):
        """Get Mastodon connector details."""
        response = await client.get("/api/connectors/mastodon")

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "mastodon"

    @pytest.mark.asyncio
    async def test_get_html_connector(self, client: AsyncClient):
        """Get HTML connector details."""
        response = await client.get("/api/connectors/html")

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "html"

    @pytest.mark.asyncio
    async def test_get_telegram_connector(self, client: AsyncClient):
        """Get Telegram connector details."""
        response = await client.get("/api/connectors/telegram")

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "telegram"

    @pytest.mark.asyncio
    async def test_get_linkedin_connector(self, client: AsyncClient):
        """Get LinkedIn connector details."""
        response = await client.get("/api/connectors/linkedin")

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "linkedin"

    @pytest.mark.asyncio
    async def test_get_instagram_connector(self, client: AsyncClient):
        """Get Instagram connector details."""
        response = await client.get("/api/connectors/instagram_scraper")

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "instagram_scraper"

    @pytest.mark.asyncio
    async def test_get_nonexistent_connector(self, client: AsyncClient):
        """Returns 422 for invalid connector type (enum validation)."""
        response = await client.get("/api/connectors/nonexistent")

        assert response.status_code == 422


class TestValidateConnector:
    """Tests for POST /api/connectors/{type}/validate endpoint."""

    @pytest.mark.asyncio
    async def test_validate_rss_valid(self, client: AsyncClient):
        """Validate valid RSS config."""
        response = await client.post(
            "/api/connectors/rss/validate",
            json={"url": "https://example.com/feed.xml"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "valid" in data

    @pytest.mark.asyncio
    async def test_validate_rss_missing_url(self, client: AsyncClient):
        """Validate RSS config missing URL."""
        response = await client.post("/api/connectors/rss/validate", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert "message" in data

    @pytest.mark.asyncio
    async def test_validate_x_scraper_valid(self, client: AsyncClient):
        """Validate valid X scraper config."""
        response = await client.post(
            "/api/connectors/x_scraper/validate",
            json={"handle": "@example"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "valid" in data

    @pytest.mark.asyncio
    async def test_validate_bluesky_valid(self, client: AsyncClient):
        """Validate valid Bluesky config."""
        response = await client.post(
            "/api/connectors/bluesky/validate",
            json={"handle": "example.bsky.social"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "valid" in data

    @pytest.mark.asyncio
    async def test_validate_nonexistent_connector(self, client: AsyncClient):
        """Validate for invalid connector type returns 422 (enum validation)."""
        response = await client.post(
            "/api/connectors/nonexistent/validate",
            json={"url": "https://example.com"},
        )

        assert response.status_code == 422
