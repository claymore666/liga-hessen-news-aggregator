"""Tests for classifier background worker."""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from models import Priority
from services.classifier_worker import (
    ClassifierWorker,
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

    def test_pause(self, worker):
        """Pause should set paused flag."""
        worker.pause()
        assert worker._paused is True

    def test_resume(self, worker):
        """Resume should clear paused flag."""
        worker._paused = True
        worker.resume()
        assert worker._paused is False


class TestClassifierWorkerStatus:
    """Tests for status reporting."""

    def test_get_status_initial(self, worker):
        """Should return initial status."""
        status = worker.get_status()
        assert status["running"] is False
        assert status["paused"] is False
        assert "stats" in status

    @pytest.mark.asyncio
    async def test_get_status_running(self, worker):
        """Should reflect running state."""
        await worker.start()
        try:
            status = worker.get_status()
            assert status["running"] is True
        finally:
            await worker.stop()

    def test_get_status_stats_copy(self, worker):
        """Should return copy of stats."""
        status = worker.get_status()
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
                with patch("services.classifier_worker.db_write_lock"):
                    with patch("services.item_events.record_event", new_callable=AsyncMock):
                        result = await worker._process_unclassified_items()

        assert result == 1
        assert worker._stats["processed"] == 1


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
