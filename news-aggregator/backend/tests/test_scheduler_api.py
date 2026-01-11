"""Tests for scheduler API endpoints."""

import pytest
from httpx import AsyncClient


class TestSchedulerStatus:
    """Tests for GET /api/scheduler/status endpoint."""

    @pytest.mark.asyncio
    async def test_scheduler_status(self, client: AsyncClient):
        """Get scheduler status."""
        response = await client.get("/api/scheduler/status")

        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert "jobs" in data
        assert "fetch_interval_minutes" in data
        assert isinstance(data["jobs"], list)

    @pytest.mark.asyncio
    async def test_scheduler_status_jobs_format(self, client: AsyncClient):
        """Scheduler jobs have expected format."""
        response = await client.get("/api/scheduler/status")

        assert response.status_code == 200
        data = response.json()
        # Jobs should be a list, may be empty if scheduler not running
        assert isinstance(data["jobs"], list)


class TestSchedulerControl:
    """Tests for scheduler start/stop endpoints."""

    @pytest.mark.asyncio
    async def test_start_scheduler(self, client: AsyncClient):
        """POST /api/scheduler/start starts scheduler."""
        response = await client.post("/api/scheduler/start")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ("started", "already_running")

    @pytest.mark.asyncio
    async def test_start_scheduler_already_running(self, client: AsyncClient):
        """Starting already running scheduler returns appropriate status."""
        # Start first
        await client.post("/api/scheduler/start")

        # Start again
        response = await client.post("/api/scheduler/start")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "already_running"

    @pytest.mark.skip(reason="APScheduler event loop issues in test environment")
    @pytest.mark.asyncio
    async def test_stop_scheduler(self, client: AsyncClient):
        """POST /api/scheduler/stop stops scheduler."""
        # Ensure scheduler is running first
        await client.post("/api/scheduler/start")

        response = await client.post("/api/scheduler/stop")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ("stopped", "already_stopped")

    @pytest.mark.skip(reason="APScheduler event loop issues in test environment")
    @pytest.mark.asyncio
    async def test_stop_scheduler_already_stopped(self, client: AsyncClient):
        """Stopping already stopped scheduler returns appropriate status."""
        # Stop first (in case it's running)
        await client.post("/api/scheduler/stop")

        # Stop again
        response = await client.post("/api/scheduler/stop")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "already_stopped"


class TestSchedulerInterval:
    """Tests for PUT /api/scheduler/interval endpoint."""

    @pytest.mark.asyncio
    async def test_update_interval_scheduler_not_running(self, client: AsyncClient):
        """Updating interval when scheduler not running returns 404."""
        # Ensure scheduler is stopped
        await client.post("/api/scheduler/stop")

        response = await client.put(
            "/api/scheduler/interval", json={"minutes": 15}
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.skip(reason="Requires scheduler job to be running - needs mocking")
    @pytest.mark.asyncio
    async def test_update_interval_success(self, client: AsyncClient):
        """Update fetch interval when scheduler is running."""
        # Start scheduler
        await client.post("/api/scheduler/start")

        response = await client.put(
            "/api/scheduler/interval", json={"minutes": 15}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["new_interval_minutes"] == 15

        # Clean up
        await client.post("/api/scheduler/stop")

    @pytest.mark.asyncio
    async def test_update_interval_invalid_minutes(self, client: AsyncClient):
        """Invalid minutes value is rejected."""
        # Too low
        response = await client.put(
            "/api/scheduler/interval", json={"minutes": 0}
        )
        assert response.status_code == 422

        # Too high
        response = await client.put(
            "/api/scheduler/interval", json={"minutes": 2000}
        )
        assert response.status_code == 422

        # Negative
        response = await client.put(
            "/api/scheduler/interval", json={"minutes": -5}
        )
        assert response.status_code == 422

    @pytest.mark.skip(reason="Requires scheduler job to be running - needs mocking")
    @pytest.mark.asyncio
    async def test_update_interval_edge_values(self, client: AsyncClient):
        """Test edge values for interval."""
        # Start scheduler
        await client.post("/api/scheduler/start")

        # Minimum valid value (1 minute)
        response = await client.put(
            "/api/scheduler/interval", json={"minutes": 1}
        )
        assert response.status_code == 200

        # Maximum valid value (1440 minutes = 24 hours)
        response = await client.put(
            "/api/scheduler/interval", json={"minutes": 1440}
        )
        assert response.status_code == 200

        # Clean up
        await client.post("/api/scheduler/stop")
