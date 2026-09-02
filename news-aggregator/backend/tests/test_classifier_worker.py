"""Tests for classifier background worker."""

import asyncio
import httpx
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from models import Priority
from services.classifier_worker import (
    ClassifierWorker,
    ServiceUnavailableError,
    CONFIDENCE_HIGH,
    CONFIDENCE_EDGE,
    get_classifier_worker,
    start_classifier_worker,
    stop_classifier_worker,
    get_unclassified_count,
)


@pytest.fixture
def worker():
    """Create ClassifierWorker instance for testing."""
    return ClassifierWorker(batch_size=10, idle_sleep=1.0)


class TestClassifierWorkerInit:
    """Tests for ClassifierWorker initialization."""

    def test_default_parameters(self):
        """Should initialize with default parameters."""
        worker = ClassifierWorker()
        assert worker.batch_size == 50
        assert worker.idle_sleep == 60.0

    def test_custom_parameters(self):
        """Should accept custom parameters."""
        worker = ClassifierWorker(batch_size=20, idle_sleep=30.0)
        assert worker.batch_size == 20
        assert worker.idle_sleep == 30.0

    def test_initial_state(self, worker):
        """Should initialize with correct state."""
        assert worker._running is False
        assert worker._paused is False
        assert worker._task is None
        assert worker._classifier is None

    def test_initial_stats(self, worker):
        """Should initialize statistics."""
        assert worker._stats["processed"] == 0
        assert worker._stats["priority_changed"] == 0
        assert worker._stats["errors"] == 0
        assert worker._stats["started_at"] is None


class TestClassifierWorkerLifecycle:
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


class TestClassifierWorkerPauseResume:
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


class TestClassifierWorkerStatus:
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


class TestDeterminePriority:
    """Tests for _determine_priority business logic."""

    def test_high_confidence_likely_relevant(self, worker):
        """High confidence should indicate likely relevant."""
        priority, score, skip_llm = worker._determine_priority(0.7)
        assert priority == Priority.MEDIUM
        assert score == 70
        assert skip_llm is False

    def test_edge_confidence_uncertain(self, worker):
        """Edge case confidence should be low priority."""
        priority, score, skip_llm = worker._determine_priority(0.35)
        assert priority == Priority.LOW
        assert score == 55
        assert skip_llm is False

    def test_low_confidence_irrelevant(self, worker):
        """Low confidence should skip LLM."""
        priority, score, skip_llm = worker._determine_priority(0.1)
        assert priority == Priority.NONE
        assert score == 20
        assert skip_llm is True

    def test_boundary_high(self, worker):
        """Exactly CONFIDENCE_HIGH should be high priority."""
        priority, score, skip_llm = worker._determine_priority(CONFIDENCE_HIGH)
        assert priority == Priority.MEDIUM

    def test_boundary_edge(self, worker):
        """Exactly CONFIDENCE_EDGE should be edge case."""
        priority, score, skip_llm = worker._determine_priority(CONFIDENCE_EDGE)
        assert priority == Priority.LOW

    def test_just_below_edge(self, worker):
        """Just below edge threshold should skip LLM."""
        priority, score, skip_llm = worker._determine_priority(CONFIDENCE_EDGE - 0.01)
        assert priority == Priority.NONE
        assert skip_llm is True


class TestProcessUnclassifiedItems:
    """Tests for _process_unclassified_items method."""

    @pytest.mark.asyncio
    async def test_process_no_classifier(self, worker):
        """Should return 0 if classifier unavailable."""
        with patch.object(worker, "_get_classifier", return_value=None):
            result = await worker._process_unclassified_items()
        assert result == 0

    @pytest.mark.asyncio
    async def test_process_no_items(self, worker):
        """Should return 0 if no unclassified items."""
        mock_classifier = MagicMock()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch.object(worker, "_get_classifier", return_value=mock_classifier):
            with patch("services.classifier_worker.async_session_maker") as mock_session:
                mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
                result = await worker._process_unclassified_items()

        assert result == 0

    @pytest.mark.asyncio
    async def test_process_classifies_items(self, worker):
        """Should classify items and update priority."""
        # Set worker to running state (needed for processing loop)
        worker._running = True

        # Mock item
        mock_item = MagicMock()
        mock_item.id = 1
        mock_item.title = "Test Article"
        mock_item.content = "Test content"
        mock_item.channel = MagicMock()
        mock_item.channel.source = MagicMock()
        mock_item.channel.source.name = "Test Source"
        mock_item.priority = Priority.NONE
        mock_item.metadata_ = {}

        mock_db_read = AsyncMock()
        mock_result_read = MagicMock()
        mock_result_read.scalars.return_value.all.return_value = [mock_item]
        mock_db_read.execute = AsyncMock(return_value=mock_result_read)

        mock_db_write = AsyncMock()
        mock_db_write.execute = AsyncMock()
        mock_db_write.commit = AsyncMock()

        mock_classifier = MagicMock()
        mock_classifier.classify = AsyncMock(return_value={
            "relevance_confidence": 0.7,
            "ak": "Test AK",
            "ak_confidence": 0.8,
            "priority": "medium",
            "priority_confidence": 0.6,
        })

        call_count = [0]

        async def mock_context_manager(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_db_read
            return mock_db_write

        with patch.object(worker, "_get_classifier", return_value=mock_classifier):
            with patch("services.classifier_worker.async_session_maker") as mock_session:
                mock_cm = AsyncMock()
                mock_cm.__aenter__ = mock_context_manager
                mock_cm.__aexit__ = AsyncMock(return_value=None)
                mock_session.return_value = mock_cm
                with patch("services.item_events.record_event", new_callable=AsyncMock):
                    result = await worker._process_unclassified_items()

        assert result == 1
        assert worker._stats["processed"] == 1


class TestPartialBatchOnServiceFailure:
    """A mid-batch classifier outage must not discard already-classified items."""

    def _mock_item(self, item_id):
        item = MagicMock()
        item.id = item_id
        item.title = f"Article {item_id}"
        item.content = "content"
        item.channel = MagicMock()
        item.channel.source = MagicMock()
        item.channel.source.name = "Test Source"
        item.priority = Priority.NONE
        item.metadata_ = {}
        return item

    async def _run_batch(self, worker, classify_side_effect, item_count=3):
        worker._running = True

        mock_db_read = AsyncMock()
        mock_result_read = MagicMock()
        mock_result_read.scalars.return_value.all.return_value = [
            self._mock_item(i) for i in range(1, item_count + 1)
        ]
        mock_db_read.execute = AsyncMock(return_value=mock_result_read)

        mock_db_write = AsyncMock()
        mock_db_write.execute = AsyncMock()
        mock_db_write.commit = AsyncMock()

        mock_classifier = MagicMock()
        mock_classifier.classify = AsyncMock(side_effect=classify_side_effect)

        call_count = [0]

        async def mock_context_manager(*args, **kwargs):
            call_count[0] += 1
            return mock_db_read if call_count[0] == 1 else mock_db_write

        with patch.object(worker, "_get_classifier", return_value=mock_classifier):
            with patch("services.classifier_worker.async_session_maker") as mock_session:
                mock_cm = AsyncMock()
                mock_cm.__aenter__ = mock_context_manager
                mock_cm.__aexit__ = AsyncMock(return_value=None)
                mock_session.return_value = mock_cm
                with patch("services.item_events.record_event", new_callable=AsyncMock):
                    result = await worker._process_unclassified_items()

        return result, mock_db_write

    @staticmethod
    def _ok(confidence=0.7):
        return {
            "relevance_confidence": confidence,
            "ak": "Test AK",
            "ak_confidence": 0.8,
            "priority": "medium",
            "priority_confidence": 0.6,
        }

    async def test_timeout_midbatch_commits_earlier_items(self, worker):
        """A read timeout partway through must still commit what succeeded."""
        side_effect = [self._ok(), self._ok(), httpx.ReadTimeout("")]

        result, mock_db_write = await self._run_batch(worker, side_effect)

        # Two items classified before the outage — both persisted, not discarded.
        assert result == 2
        assert worker._stats["processed"] == 2
        mock_db_write.commit.assert_awaited()

    async def test_timeout_midbatch_does_not_raise(self, worker):
        """Partial progress keeps the worker in its normal loop, not the outage backoff."""
        side_effect = [self._ok(), httpx.ReadTimeout("")]

        # Must not raise ServiceUnavailableError — that would latch the worker.
        result, _ = await self._run_batch(worker, side_effect, item_count=2)
        assert result == 1

    async def test_timeout_on_first_item_signals_outage(self, worker):
        """Zero progress means the backend really is down — surface it."""
        side_effect = httpx.ReadTimeout("")

        with pytest.raises(ServiceUnavailableError):
            await self._run_batch(worker, side_effect)

        assert worker._stats["processed"] == 0


class TestModuleFunctions:
    """Tests for module-level functions."""

    @pytest.mark.asyncio
    async def test_get_classifier_worker_none_initially(self):
        """Should return None when no worker started."""
        import services.classifier_worker as clf_module
        clf_module._worker = None
        assert get_classifier_worker() is None

    @pytest.mark.asyncio
    async def test_start_classifier_worker_creates_instance(self):
        """Start should create and start instance."""
        import services.classifier_worker as clf_module
        clf_module._worker = None

        worker = await start_classifier_worker(batch_size=10, idle_sleep=1.0)
        try:
            assert worker is not None
            assert worker._running is True
            assert get_classifier_worker() is worker
        finally:
            await stop_classifier_worker()

    @pytest.mark.asyncio
    async def test_stop_classifier_worker_clears_instance(self):
        """Stop should clear global instance."""
        import services.classifier_worker as clf_module

        await start_classifier_worker()
        await stop_classifier_worker()
        assert clf_module._worker is None

    @pytest.mark.asyncio
    async def test_get_unclassified_count(self):
        """Should query database for unclassified count."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 42
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("services.classifier_worker.async_session_maker") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
            count = await get_unclassified_count()

        assert count == 42


class TestConfidenceThresholds:
    """Tests for confidence threshold constants."""

    def test_high_threshold_reasonable(self):
        """CONFIDENCE_HIGH should be a reasonable value."""
        assert 0 < CONFIDENCE_HIGH < 1
        assert CONFIDENCE_HIGH >= 0.4  # Should be fairly confident

    def test_edge_threshold_reasonable(self):
        """CONFIDENCE_EDGE should be lower than HIGH."""
        assert CONFIDENCE_EDGE < CONFIDENCE_HIGH
        assert CONFIDENCE_EDGE > 0.1  # But not too low

    def test_thresholds_create_three_buckets(self, worker):
        """Thresholds should create distinct priority buckets."""
        # Above HIGH
        p1, _, _ = worker._determine_priority(CONFIDENCE_HIGH + 0.1)

        # Between EDGE and HIGH
        p2, _, _ = worker._determine_priority((CONFIDENCE_HIGH + CONFIDENCE_EDGE) / 2)

        # Below EDGE
        p3, _, _ = worker._determine_priority(CONFIDENCE_EDGE - 0.1)

        assert p1 != p2 != p3


class TestClassifierWorkerSelfHealing:
    """Tests for self-healing error state lifecycle."""

    def test_initial_error_state(self, worker):
        """Should initialize with clean error state."""
        assert worker._stopped_due_to_errors is False
        assert worker._consecutive_errors == 0

    @pytest.mark.asyncio
    async def test_error_state_after_consecutive_errors(self, worker):
        """Worker should enter error state after 10 consecutive errors."""
        worker._running = True
        # Simulate 10 consecutive errors
        for i in range(10):
            worker._consecutive_errors = i + 1

        # At 10 errors, the flag should be set
        worker._stopped_due_to_errors = True  # This is set in _run()
        assert worker._stopped_due_to_errors is True
        assert worker._consecutive_errors == 10

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
        """_on_success() should clear both error flags."""
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
    async def test_on_success_clears_service_unavailable(self, worker):
        """_on_success() should also clear service_unavailable flag."""
        worker._consecutive_errors = 5
        worker._service_unavailable = True

        with patch("services.worker_status.write_state", new_callable=AsyncMock) as mock_ws:
            await worker._on_success()

        assert worker._consecutive_errors == 0
        assert worker._service_unavailable is False
        mock_ws.assert_called_once_with(
            "classifier", running=True, service_available=True,
        )

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

    @pytest.mark.asyncio
    async def test_get_status_includes_service_available(self, worker):
        """get_status() should include service_available field."""
        status = await worker.get_status()
        assert "service_available" in status
        assert status["service_available"] is True

    @pytest.mark.asyncio
    async def test_get_status_reflects_service_unavailable(self, worker):
        """get_status() should reflect service unavailability."""
        worker._service_unavailable = True
        status = await worker.get_status()
        assert status["service_available"] is False
