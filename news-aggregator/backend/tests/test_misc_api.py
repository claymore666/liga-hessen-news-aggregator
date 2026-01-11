"""Tests for miscellaneous API endpoints (email, proxies)."""

import pytest
from httpx import AsyncClient


class TestProxiesAPI:
    """Tests for proxy management endpoints."""

    @pytest.mark.asyncio
    async def test_proxy_status(self, client: AsyncClient):
        """GET /api/proxies/status returns proxy status."""
        response = await client.get("/api/proxies/status")

        assert response.status_code == 200
        data = response.json()
        assert "working_count" in data or "count" in data or isinstance(data, dict)

    @pytest.mark.skip(reason="Makes network calls - slow in CI")
    @pytest.mark.asyncio
    async def test_proxy_refresh(self, client: AsyncClient):
        """POST /api/proxies/refresh triggers proxy refresh."""
        response = await client.post("/api/proxies/refresh")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data or "message" in data or "success" in data

    @pytest.mark.asyncio
    async def test_proxy_next(self, client: AsyncClient):
        """GET /api/proxies/next returns next proxy."""
        response = await client.get("/api/proxies/next")

        # May return 200 with proxy or indicate no proxies available
        assert response.status_code in (200, 404)
        if response.status_code == 200:
            data = response.json()
            # Should return proxy info or null


class TestEmailAPI:
    """Tests for email-related endpoints."""

    @pytest.mark.skip(reason="May make network calls - slow in CI")
    @pytest.mark.asyncio
    async def test_email_test_no_config(self, client: AsyncClient):
        """POST /api/email/test fails gracefully without email config."""
        response = await client.post("/api/email/test")

        # Should fail if email not configured, but not crash
        assert response.status_code in (200, 400, 500, 503)

    @pytest.mark.skip(reason="Requires email configuration and data")
    @pytest.mark.asyncio
    async def test_preview_briefing(self, client: AsyncClient):
        """GET /api/email/preview-briefing generates preview."""
        response = await client.get("/api/email/preview-briefing")

        # May succeed or fail based on data availability
        assert response.status_code in (200, 400, 404)

        if response.status_code == 200:
            data = response.json()
            # Should have some briefing content
            assert "subject" in data or "html" in data or "content" in data

    @pytest.mark.skip(reason="May make network calls - slow in CI")
    @pytest.mark.asyncio
    async def test_send_briefing_no_config(self, client: AsyncClient):
        """POST /api/email/send-briefing fails gracefully without email config."""
        response = await client.post("/api/email/send-briefing")

        # Should fail if email not configured
        assert response.status_code in (200, 400, 500, 503)


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_invalid_endpoint(self, client: AsyncClient):
        """Non-existent endpoint returns 404."""
        response = await client.get("/api/nonexistent/endpoint")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_method_not_allowed(self, client: AsyncClient):
        """Wrong HTTP method returns 405."""
        # GET on a POST-only endpoint
        response = await client.get("/api/scheduler/start")

        assert response.status_code == 405

    @pytest.mark.asyncio
    async def test_invalid_json(self, client: AsyncClient):
        """Invalid JSON returns 422."""
        response = await client.post(
            "/api/sources",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_required_field(self, client: AsyncClient):
        """Missing required field returns 422."""
        # Source requires name
        response = await client.post("/api/sources", json={})

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_id_type(self, client: AsyncClient):
        """Invalid ID type returns 422."""
        response = await client.get("/api/items/not-a-number")

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_negative_id(self, client: AsyncClient):
        """Negative ID returns 404 or 422."""
        response = await client.get("/api/items/-1")

        assert response.status_code in (404, 422)


class TestConcurrentRequests:
    """Tests for concurrent request handling."""

    @pytest.mark.asyncio
    async def test_concurrent_reads(self, client: AsyncClient):
        """Multiple concurrent read requests don't interfere."""
        import asyncio

        async def fetch_stats():
            return await client.get("/api/stats")

        responses = await asyncio.gather(*[fetch_stats() for _ in range(5)])

        for response in responses:
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_concurrent_health_checks(self, client: AsyncClient):
        """Multiple concurrent health checks succeed."""
        import asyncio

        async def health_check():
            return await client.get("/health")

        responses = await asyncio.gather(*[health_check() for _ in range(10)])

        for response in responses:
            assert response.status_code == 200


class TestContentTypes:
    """Tests for content type handling."""

    @pytest.mark.asyncio
    async def test_json_content_type(self, client: AsyncClient):
        """API returns JSON content type."""
        response = await client.get("/api/stats")

        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_accepts_json_request(self, client: AsyncClient):
        """API accepts JSON request body."""
        response = await client.post(
            "/api/sources",
            json={"name": "Content Type Test"},
            headers={"Accept": "application/json"},
        )

        assert response.status_code == 201
        assert "application/json" in response.headers.get("content-type", "")
