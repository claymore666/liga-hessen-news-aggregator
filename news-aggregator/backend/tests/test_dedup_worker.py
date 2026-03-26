"""Tests for deduplication background worker."""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from services.dedup_worker import (
    DedupWorker,
    get_dedup_worker,
    start_dedup_worker,
    stop_dedup_worker,
)


@pytest.fixture
def worker():
    """Create DedupWorker instance for testing."""
    return DedupWorker(batch_size=10, idle_sleep=1.0)


class TestDedupWorkerInit:
    """Tests for DedupWorker initialization."""

    def test_default_parameters(self):
        """Should initialize with default parameters."""
        worker = DedupWorker()
        assert worker.batch_size == 50
        assert worker.idle_sleep == 30.0

    def test_custom_parameters(self):
        """Should accept custom parameters."""
        worker = DedupWorker(batch_size=20, idle_sleep=15.0)
        assert worker.batch_size == 20
        assert worker.idle_sleep == 15.0

    def test_initial_state(self, worker):
        """Should initialize with correct state."""
        assert worker._running is False
        assert worker._paused is False
        assert worker._task is None
        assert worker._classifier is None

    def test_initial_stats(self, worker):
        """Should initialize statistics."""
        assert worker._stats["phase1_checked"] == 0
        assert worker._stats["phase2_checked"] == 0
        assert worker._stats["duplicates_found"] == 0
        assert worker._stats["vectordb_indexed"] == 0
        assert worker._stats["errors"] == 0
        assert worker._stats["started_at"] is None


class TestDedupWorkerLifecycle:
    """Tests for worker start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_sets_running(self, worker):
        """Start should set running flag and create task."""
        await worker.start()
        try:
            assert worker._running is True
            assert worker._task is not None
            assert worker._stats["started_at"] is not None
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent(self, worker):
        """Starting already running worker should be safe."""
        await worker.start()
        try:
            task1 = worker._task
            await worker.start()  # Second start
            assert worker._task == task1
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self, worker):
        """Stop should clear running flag."""
        await worker.start()
        await worker.stop()
        assert worker._running is False

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, worker):
        """Stopping non-running worker should be safe."""
        await worker.stop()
        assert worker._running is False


class TestDedupWorkerPauseResume:
    """Tests for pause/resume functionality."""

    @pytest.mark.asyncio
    async def test_pause(self, worker):
        """Pause should set paused flag."""
        with patch("services.worker_status.write_state", new_callable=AsyncMock):
            await worker.pause()
        assert worker._paused is True

    @pytest.mark.asyncio
    async def test_resume(self, worker):
        """Resume should clear paused flag."""
        worker._paused = True
        with patch("services.worker_status.write_state", new_callable=AsyncMock):
            await worker.resume()
        assert worker._paused is False


class TestDedupWorkerStatus:
    """Tests for status reporting."""

    @pytest.mark.asyncio
    async def test_get_status_initial(self, worker):
        """Should return initial status."""
        status = await worker.get_status()
        assert status["running"] is False
        assert status["paused"] is False
        assert "stats" in status

    @pytest.mark.asyncio
    async def test_get_status_running(self, worker):
        """Should reflect running state."""
        await worker.start()
        try:
            status = await worker.get_status()
            assert status["running"] is True
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_get_status_stats_copy(self, worker):
        """Should return copy of stats."""
        status = await worker.get_status()
        status["stats"]["errors"] = 999
        assert worker._stats["errors"] == 0


class TestDedupWorkerSelfHealing:
    """Tests for self-healing error state lifecycle."""

    def test_initial_error_state(self, worker):
        """Should initialize with clean error state."""
        assert worker._stopped_due_to_errors is False
        assert worker._consecutive_errors == 0

    @pytest.mark.asyncio
    async def test_error_state_does_not_stop_worker(self, worker):
        """Error state should NOT call stop() -- worker keeps running."""
        await worker.start()
        try:
            # Simulate entering error state
            worker._consecutive_errors = 10
            worker._stopped_due_to_errors = True

            # Worker should still be running
            assert worker._running is True
            assert worker._task is not None
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_on_success_clears_error_state(self, worker):
        """_on_success() should clear both _stopped_due_to_errors and _consecutive_errors."""
        worker._consecutive_errors = 10
        worker._stopped_due_to_errors = True

        with patch("services.worker_status.write_state", new_callable=AsyncMock):
            await worker._on_success()

        assert worker._consecutive_errors == 0
        assert worker._stopped_due_to_errors is False

    @pytest.mark.asyncio
    async def test_on_success_noop_when_no_errors(self, worker):
        """_on_success() should be safe when no error state."""
        worker._consecutive_errors = 0
        worker._stopped_due_to_errors = False

        # Should not call write_state when not in error state
        with patch("services.worker_status.write_state", new_callable=AsyncMock) as mock_ws:
            await worker._on_success()
            mock_ws.assert_not_called()

        assert worker._consecutive_errors == 0
        assert worker._stopped_due_to_errors is False

    @pytest.mark.asyncio
    async def test_on_success_writes_state_when_recovering(self, worker):
        """_on_success() should write state when recovering from errors."""
        worker._consecutive_errors = 10
        worker._stopped_due_to_errors = True

        with patch("services.worker_status.write_state", new_callable=AsyncMock) as mock_ws:
            await worker._on_success()
            mock_ws.assert_called_once_with("dedup", running=True)

    @pytest.mark.asyncio
    async def test_resume_clears_error_state(self, worker):
        """resume() should reset both _stopped_due_to_errors and _consecutive_errors."""
        worker._consecutive_errors = 10
        worker._stopped_due_to_errors = True

        with patch("services.worker_status.write_state", new_callable=AsyncMock):
            await worker.resume()

        assert worker._paused is False
        assert worker._stopped_due_to_errors is False
        assert worker._consecutive_errors == 0

    @pytest.mark.asyncio
    async def test_resume_clears_error_state_even_when_not_paused(self, worker):
        """resume() should clear error state even if worker wasn't paused."""
        worker._paused = False
        worker._consecutive_errors = 10
        worker._stopped_due_to_errors = True

        with patch("services.worker_status.write_state", new_callable=AsyncMock):
            await worker.resume()

        assert worker._stopped_due_to_errors is False
        assert worker._consecutive_errors == 0

    @pytest.mark.asyncio
    async def test_start_resets_error_state(self, worker):
        """start() should reset both error flags."""
        worker._consecutive_errors = 10
        worker._stopped_due_to_errors = True

        await worker.start()
        try:
            assert worker._stopped_due_to_errors is False
            assert worker._consecutive_errors == 0
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_get_status_includes_stopped_due_to_errors(self, worker):
        """get_status() should include stopped_due_to_errors field."""
        status = await worker.get_status()
        assert "stopped_due_to_errors" in status
        assert status["stopped_due_to_errors"] is False

    @pytest.mark.asyncio
    async def test_get_status_reflects_error_state(self, worker):
        """get_status() should reflect current error state."""
        worker._stopped_due_to_errors = True
        status = await worker.get_status()
        assert status["stopped_due_to_errors"] is True


class TestModuleFunctions:
    """Tests for module-level functions."""

    @pytest.mark.asyncio
    async def test_get_dedup_worker_none_initially(self):
        """Should return None when no worker started."""
        import services.dedup_worker as dedup_module
        dedup_module._worker = None
        assert get_dedup_worker() is None

    @pytest.mark.asyncio
    async def test_start_dedup_worker_creates_instance(self):
        """Start should create and start instance."""
        import services.dedup_worker as dedup_module
        dedup_module._worker = None

        worker = await start_dedup_worker(batch_size=10, idle_sleep=1.0)
        try:
            assert worker is not None
            assert worker._running is True
            assert get_dedup_worker() is worker
        finally:
            await stop_dedup_worker()

    @pytest.mark.asyncio
    async def test_stop_dedup_worker_clears_instance(self):
        """Stop should clear global instance."""
        import services.dedup_worker as dedup_module

        await start_dedup_worker()
        await stop_dedup_worker()
        assert dedup_module._worker is None
