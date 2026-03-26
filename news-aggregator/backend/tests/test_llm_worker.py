"""Tests for LLM processing worker."""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from services.llm_worker import (
    LLMWorker,
    get_worker,
    start_worker,
    stop_worker,
    enqueue_fresh_item,
)


@pytest.fixture
def worker():
    """Create LLMWorker instance for testing."""
    return LLMWorker(batch_size=5, idle_sleep=1.0, backlog_batch_size=10)


class TestLLMWorkerInit:
    """Tests for LLMWorker initialization."""

    def test_default_parameters(self):
        """Should initialize with default parameters."""
        worker = LLMWorker()
        assert worker.batch_size == 10
        assert worker.idle_sleep == 30.0
        assert worker.backlog_batch_size == 50

    def test_custom_parameters(self):
        """Should accept custom parameters."""
        worker = LLMWorker(batch_size=5, idle_sleep=10.0, backlog_batch_size=20)
        assert worker.batch_size == 5
        assert worker.idle_sleep == 10.0
        assert worker.backlog_batch_size == 20

    def test_initial_state(self, worker):
        """Should initialize with correct state."""
        assert worker._running is False
        assert worker._paused is False
        assert worker._task is None
        assert worker._processor is None

    def test_initial_stats(self, worker):
        """Should initialize statistics."""
        assert worker._stats["fresh_processed"] == 0
        assert worker._stats["backlog_processed"] == 0
        assert worker._stats["errors"] == 0
        assert worker._stats["started_at"] is None


class TestLLMWorkerLifecycle:
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
            # Task should remain the same
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
        await worker.stop()  # Should not raise
        assert worker._running is False


class TestLLMWorkerPauseResume:
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

    @pytest.mark.asyncio
    async def test_pause_resume_cycle(self, worker):
        """Should handle pause/resume cycle."""
        assert worker._paused is False
        with patch("services.worker_status.write_state", new_callable=AsyncMock):
            await worker.pause()
            assert worker._paused is True
            await worker.resume()
            assert worker._paused is False


class TestLLMWorkerEnqueue:
    """Tests for fresh item enqueueing."""

    @pytest.mark.asyncio
    async def test_enqueue_fresh(self, worker):
        """Should add item to fresh queue."""
        await worker.enqueue_fresh(123)
        assert worker._fresh_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_enqueue_multiple(self, worker):
        """Should handle multiple enqueues."""
        for i in range(5):
            await worker.enqueue_fresh(i)
        assert worker._fresh_queue.qsize() == 5

    @pytest.mark.asyncio
    async def test_enqueue_order_preserved(self, worker):
        """Should preserve FIFO order."""
        await worker.enqueue_fresh(1)
        await worker.enqueue_fresh(2)
        await worker.enqueue_fresh(3)

        assert worker._fresh_queue.get_nowait() == 1
        assert worker._fresh_queue.get_nowait() == 2
        assert worker._fresh_queue.get_nowait() == 3


class TestLLMWorkerStatus:
    """Tests for status reporting."""

    @pytest.mark.asyncio
    async def test_get_status_initial(self, worker):
        """Should return initial status."""
        status = await worker.get_status()
        assert status["running"] is False
        assert status["paused"] is False
        assert status["fresh_queue_size"] == 0
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
    async def test_get_status_queue_size(self, worker):
        """Should reflect queue size."""
        await worker.enqueue_fresh(1)
        await worker.enqueue_fresh(2)
        status = await worker.get_status()
        assert status["fresh_queue_size"] == 2

    @pytest.mark.asyncio
    async def test_get_status_stats_copy(self, worker):
        """Should return copy of stats."""
        status = await worker.get_status()
        status["stats"]["errors"] = 999
        # Original should be unchanged
        assert worker._stats["errors"] == 0


class TestLLMWorkerProcessFresh:
    """Tests for fresh item processing."""

    @pytest.mark.asyncio
    async def test_process_fresh_empty_queue(self, worker):
        """Should return 0 for empty queue."""
        result = await worker._process_fresh_items()
        assert result == 0

    @pytest.mark.asyncio
    async def test_process_fresh_no_processor(self, worker):
        """Should re-enqueue items if processor unavailable."""
        await worker.enqueue_fresh(1)
        await worker.enqueue_fresh(2)

        with patch.object(worker, "_get_processor", return_value=None):
            result = await worker._process_fresh_items()

        assert result == 0
        # Items should be re-enqueued
        assert worker._fresh_queue.qsize() == 2

    @pytest.mark.asyncio
    async def test_process_fresh_batch_limit(self, worker):
        """Should respect batch size limit."""
        # Enqueue more than batch_size items
        for i in range(10):
            await worker.enqueue_fresh(i)

        # Mock processor and _process_items
        mock_processor = MagicMock()
        with patch.object(worker, "_get_processor", return_value=mock_processor):
            with patch.object(worker, "_process_items", return_value=5) as mock_process:
                result = await worker._process_fresh_items()

        # Should process up to batch_size
        call_args = mock_process.call_args
        item_ids = call_args[0][0]
        assert len(item_ids) <= worker.batch_size


class TestLLMWorkerProcessBacklog:
    """Tests for backlog item processing."""

    @pytest.mark.asyncio
    async def test_process_backlog_no_processor(self, worker):
        """Should return 0 if processor unavailable."""
        with patch.object(worker, "_get_processor", return_value=None):
            result = await worker._process_backlog_items()
        assert result == 0

    @pytest.mark.asyncio
    async def test_process_backlog_queries_database(self, worker):
        """Should query database for backlog items."""
        mock_processor = MagicMock()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(1,), (2,), (3,)]
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch.object(worker, "_get_processor", return_value=mock_processor):
            with patch("services.llm_worker.async_session_maker") as mock_session:
                mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
                with patch.object(worker, "_process_items", return_value=3):
                    result = await worker._process_backlog_items()

        assert result == 3


class TestLLMWorkerProcessItems:
    """Tests for item processing logic."""

    @pytest.mark.asyncio
    async def test_process_items_updates_stats(self, worker):
        """Should update statistics after processing."""
        # Create mock item
        mock_item = MagicMock()
        mock_item.id = 1
        mock_item.title = "Test Article"
        mock_item.content = "Test content"
        mock_item.url = "https://test.com/1"
        mock_item.channel = MagicMock()
        mock_item.channel.source = MagicMock()
        mock_item.channel.source.name = "Test Source"
        mock_item.needs_llm_processing = True
        mock_item.metadata_ = {}
        mock_item.priority_score = 50
        mock_item.assigned_aks = None
        mock_item.published_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_item

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        analysis = {
            "summary": "Test summary",
            "priority": "medium",
            "relevant": True,
            "relevance_score": 0.7,
            "assigned_aks": [],
            "tags": [],
        }
        mock_processor = MagicMock()
        mock_processor.analyze_from_data_with_messages = AsyncMock(
            return_value=(analysis, [{"role": "assistant", "content": "{}"}])
        )
        mock_processor.extract_topics = AsyncMock(return_value=("Sonstiges", None))

        with patch("services.llm_worker.async_session_maker") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch("services.item_events.record_event", new_callable=AsyncMock):
                result = await worker._process_items([1], mock_processor, is_fresh=True)

        assert result == 1
        assert worker._stats["last_processed_at"] is not None

    @pytest.mark.asyncio
    async def test_process_items_skips_missing(self, worker):
        """Should skip items not found in database."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_processor = MagicMock()

        with patch("services.llm_worker.async_session_maker") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await worker._process_items([999], mock_processor, is_fresh=True)

        assert result == 0

    @pytest.mark.asyncio
    async def test_process_items_respects_pause(self, worker):
        """Should stop processing when paused."""
        worker._paused = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_processor = MagicMock()

        with patch("services.llm_worker.async_session_maker") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await worker._process_items([1, 2, 3], mock_processor, is_fresh=False)

        # Should stop immediately due to pause
        assert result == 0


class TestModuleFunctions:
    """Tests for module-level functions."""

    @pytest.mark.asyncio
    async def test_get_worker_none_initially(self):
        """Should return None when no worker started."""
        # Reset global state
        import services.llm_worker as llm_module
        llm_module._worker = None

        assert get_worker() is None

    @pytest.mark.asyncio
    async def test_start_worker_creates_instance(self):
        """Start worker should create and start instance."""
        import services.llm_worker as llm_module
        llm_module._worker = None

        worker = await start_worker(batch_size=5, idle_sleep=1.0)
        try:
            assert worker is not None
            assert worker._running is True
            assert get_worker() is worker
        finally:
            await stop_worker()

    @pytest.mark.asyncio
    async def test_stop_worker_clears_instance(self):
        """Stop worker should clear global instance."""
        import services.llm_worker as llm_module

        await start_worker()
        await stop_worker()
        assert llm_module._worker is None

    @pytest.mark.asyncio
    async def test_enqueue_fresh_item_with_worker(self):
        """Should enqueue item when worker running."""
        await start_worker()
        try:
            await enqueue_fresh_item(123)
            worker = get_worker()
            assert worker._fresh_queue.qsize() == 1
        finally:
            await stop_worker()

    @pytest.mark.asyncio
    async def test_enqueue_fresh_item_no_worker(self):
        """Should log warning when no worker available."""
        import services.llm_worker as llm_module
        llm_module._worker = None

        # Should not raise, just log warning
        await enqueue_fresh_item(123)


class TestPriorityMapping:
    """Tests for LLM priority to item priority mapping."""

    @pytest.fixture
    def mock_item(self):
        """Create a mock item for priority testing."""
        item = MagicMock()
        item.id = 1
        item.title = "Test Article"
        item.channel = MagicMock()
        item.channel.source = MagicMock()
        item.channel.source.name = "Test Source"
        item.needs_llm_processing = True
        item.metadata_ = {}
        item.priority_score = 50
        item.assigned_aks = None
        return item

    def _make_mock_db(self, mock_item):
        """Create mock DB that tracks sql_update values."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_item

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        return mock_db

    def _get_update_values(self, mock_db):
        """Extract the values dict from the sql_update call.

        The sql_update call is the second db.execute call (first is the SELECT).
        """
        calls = mock_db.execute.call_args_list
        # Find the call that has a .values() clause (the UPDATE, not the SELECT)
        for call in calls:
            stmt = call[0][0] if call[0] else None
            if stmt is not None and hasattr(stmt, 'compile'):
                # Try to get the parameters from an UPDATE statement
                try:
                    # SQLAlchemy Update objects store parameters internally
                    if hasattr(stmt, '_values'):
                        return dict(stmt._values)
                except Exception:
                    pass
        return None

    @pytest.mark.asyncio
    async def test_high_priority_mapping(self, worker, mock_item):
        """High LLM priority should map to HIGH."""
        from models import Priority

        mock_item.content = "Test content"
        mock_item.url = "https://test.com/1"
        mock_item.published_at = None

        mock_db = self._make_mock_db(mock_item)

        analysis = {
            "summary": "Test",
            "priority": "high",
            "relevant": True,
            "relevance_score": 0.9,
            "assigned_aks": [],
            "tags": [],
        }
        mock_processor = MagicMock()
        mock_processor.analyze_from_data_with_messages = AsyncMock(
            return_value=(analysis, [{"role": "assistant", "content": "{}"}])
        )
        mock_processor.extract_topics = AsyncMock(return_value=("Sonstiges", None))

        with patch("services.llm_worker.async_session_maker") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch("services.item_events.record_event", new_callable=AsyncMock):
                await worker._process_items([1], mock_processor, is_fresh=True)

        # Verify via sql_update call args - find the UPDATE execute call
        update_calls = [c for c in mock_db.execute.call_args_list if len(c[0]) > 0]
        # The last execute before commit should be the UPDATE
        update_stmt = update_calls[-1][0][0]
        params = update_stmt.compile().params
        assert params["priority"] == Priority.HIGH
        assert params["priority_score"] >= 90

    @pytest.mark.asyncio
    async def test_medium_priority_mapping(self, worker, mock_item):
        """Medium LLM priority should map to MEDIUM."""
        from models import Priority

        mock_item.content = "Test content"
        mock_item.url = "https://test.com/1"
        mock_item.published_at = None

        mock_db = self._make_mock_db(mock_item)

        analysis = {
            "summary": "Test",
            "priority": "medium",
            "relevant": True,
            "relevance_score": 0.6,
            "assigned_aks": [],
            "tags": [],
        }
        mock_processor = MagicMock()
        mock_processor.analyze_from_data_with_messages = AsyncMock(
            return_value=(analysis, [{"role": "assistant", "content": "{}"}])
        )
        mock_processor.extract_topics = AsyncMock(return_value=("Sonstiges", None))

        with patch("services.llm_worker.async_session_maker") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch("services.item_events.record_event", new_callable=AsyncMock):
                await worker._process_items([1], mock_processor, is_fresh=True)

        update_calls = [c for c in mock_db.execute.call_args_list if len(c[0]) > 0]
        update_stmt = update_calls[-1][0][0]
        params = update_stmt.compile().params
        assert params["priority"] == Priority.MEDIUM

    @pytest.mark.asyncio
    async def test_irrelevant_priority_mapping(self, worker, mock_item):
        """Irrelevant items should map to NONE."""
        from models import Priority

        mock_item.content = "Test content"
        mock_item.url = "https://test.com/1"
        mock_item.published_at = None

        mock_db = self._make_mock_db(mock_item)

        analysis = {
            "summary": "Test",
            "priority": "high",  # Even with high priority
            "relevant": False,  # Marked as irrelevant
            "relevance_score": 0.1,
            "assigned_aks": [],
            "tags": [],
        }
        mock_processor = MagicMock()
        mock_processor.analyze_from_data_with_messages = AsyncMock(
            return_value=(analysis, [{"role": "assistant", "content": "{}"}])
        )
        mock_processor.extract_topics = AsyncMock(return_value=("Sonstiges", None))

        with patch("services.llm_worker.async_session_maker") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch("services.item_events.record_event", new_callable=AsyncMock):
                await worker._process_items([1], mock_processor, is_fresh=True)

        update_calls = [c for c in mock_db.execute.call_args_list if len(c[0]) > 0]
        update_stmt = update_calls[-1][0][0]
        params = update_stmt.compile().params
        assert params["priority"] == Priority.NONE


class TestLLMWorkerSelfHealing:
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
            mock_ws.assert_called_once_with("llm", running=True)

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
