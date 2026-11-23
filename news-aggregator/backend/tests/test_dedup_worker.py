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


# =============================================================================
# Phase 1 / Phase 2 behaviour (against the test database)
# =============================================================================

from datetime import timedelta  # noqa: E402

import pytest_asyncio  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from database import utcnow  # noqa: E402
from models import Channel, ConnectorType, Item, Priority, Source  # noqa: E402
from services.dedup_groups import DUPLICATE_THRESHOLD_MAYBE  # noqa: E402

PHASE1_DONE = {"dedup_phase1": True, "duplicate_checked": True}


@pytest_asyncio.fixture
async def maker(db_engine):
    """Session maker on the test DB, also used by the worker."""
    session_maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    with patch("services.dedup_worker.async_session_maker", session_maker):
        yield session_maker


@pytest_asyncio.fixture
async def channels(maker) -> list[int]:
    """Two channels of one source; returns their ids."""
    async with maker() as db:
        source = Source(name="Dedup Source", enabled=True)
        db.add(source)
        await db.flush()
        result = []
        for n in (1, 2):
            channel = Channel(
                source_id=source.id,
                name=f"Channel {n}",
                connector_type=ConnectorType.RSS,
                config={"url": f"https://feed{n}.example/rss"},
                source_identifier=f"https://feed{n}.example/rss",
                enabled=True,
            )
            db.add(channel)
            await db.flush()
            result.append(channel.id)
        await db.commit()
    return result


_counter = 0


async def add_item(maker, channel_id: int, title: str, **kwargs) -> int:
    """Insert one item and return its id (ids ascend in call order)."""
    global _counter
    _counter += 1
    values = {
        "channel_id": channel_id,
        "external_id": f"ext-{_counter}",
        "title": title,
        "content": f"content {_counter}",
        "url": f"https://example.com/article/{_counter}",
        "published_at": utcnow(),
        "content_hash": f"hash-{_counter}",
        "priority": Priority.NONE,
        "metadata_": {},
    }
    values.update(kwargs)
    async with maker() as db:
        item = Item(**values)
        db.add(item)
        await db.commit()
        return item.id


async def get_item(maker, item_id: int) -> Item:
    async with maker() as db:
        return (await db.execute(select(Item).where(Item.id == item_id))).scalar_one()


def make_classifier(result) -> MagicMock:
    """Classifier mock; ``result`` is a return value or a function of the item id."""
    classifier = MagicMock()
    if callable(result):
        async def lookup(item_id, candidate_ids, threshold=0.75, n_results=5):
            return result(item_id)
        classifier.find_duplicates_by_id = AsyncMock(side_effect=lookup)
    else:
        classifier.find_duplicates_by_id = AsyncMock(return_value=result)
    classifier.find_duplicates = AsyncMock(side_effect=AssertionError("text lookup is gone"))
    return classifier


@pytest.fixture
def running_worker():
    worker = DedupWorker(batch_size=10, idle_sleep=1.0)
    worker._running = True
    return worker


class TestPhase1:
    """Phase 1: exact matches only, always linked to the top item."""

    @pytest.mark.asyncio
    async def test_url_match_links_to_top_item_of_match(self, running_worker, maker, channels):
        top = await add_item(maker, channels[0], "Top", metadata_=dict(PHASE1_DONE))
        copy = await add_item(
            maker, channels[0], "Copy", url="https://example.com/story",
            similar_to_id=top, metadata_=dict(PHASE1_DONE),
        )
        new = await add_item(maker, channels[1], "New", url="https://example.com/story")

        assert await running_worker._process_phase1_dedup() == 1

        item = await get_item(maker, new)
        assert item.similar_to_id == top  # not the copy it matched
        assert item.metadata_["duplicate_method"] == "url_match"
        assert item.metadata_["dedup_phase1"] is True
        assert item.metadata_["duplicate_checked"] is True
        assert (await get_item(maker, copy)).similar_to_id == top
        assert running_worker._stats["duplicates_found"] == 1

    @pytest.mark.asyncio
    async def test_same_url_on_same_channel_is_not_a_duplicate(
        self, running_worker, maker, channels
    ):
        await add_item(
            maker, channels[0], "First", url="https://example.com/s", metadata_=dict(PHASE1_DONE)
        )
        new = await add_item(maker, channels[0], "Second", url="https://example.com/s")

        await running_worker._process_phase1_dedup()

        assert (await get_item(maker, new)).similar_to_id is None

    @pytest.mark.asyncio
    async def test_content_hash_match_links_to_top_item_of_match(
        self, running_worker, maker, channels
    ):
        top = await add_item(maker, channels[0], "Top", metadata_=dict(PHASE1_DONE))
        await add_item(
            maker, channels[0], "Copy", content_hash="same-hash",
            similar_to_id=top, metadata_=dict(PHASE1_DONE),
        )
        new = await add_item(maker, channels[0], "New", content_hash="same-hash")

        await running_worker._process_phase1_dedup()

        item = await get_item(maker, new)
        assert item.similar_to_id == top
        assert item.metadata_["duplicate_method"] == "content_hash"

    @pytest.mark.asyncio
    async def test_similar_title_alone_does_not_link(self, running_worker, maker, channels):
        title = "Vielen Kitas in Hessen fehlen weiterhin Fachkräfte"
        await add_item(maker, channels[0], title, metadata_=dict(PHASE1_DONE))
        new = await add_item(maker, channels[1], title)

        assert await running_worker._process_phase1_dedup() == 1

        item = await get_item(maker, new)
        assert item.similar_to_id is None
        assert "duplicate_method" not in item.metadata_
        assert item.metadata_["dedup_phase1"] is True
        assert running_worker._stats["duplicates_found"] == 0

    @pytest.mark.asyncio
    async def test_items_of_one_batch_do_not_chain(self, running_worker, maker, channels):
        """The second copy of a batch must see the link of the first one."""
        top = await add_item(
            maker, channels[0], "Top", content_hash="h1", metadata_=dict(PHASE1_DONE)
        )
        first = await add_item(
            maker, channels[0], "First", content_hash="h1", url="https://example.com/x"
        )
        second = await add_item(maker, channels[1], "Second", url="https://example.com/x")

        assert await running_worker._process_phase1_dedup() == 2

        assert (await get_item(maker, first)).similar_to_id == top
        assert (await get_item(maker, second)).similar_to_id == top


class TestPhase2:
    """Phase 2: id-based similarity lookup + title rule."""

    @pytest.mark.asyncio
    async def test_confirmed_match_links_to_top_item(self, running_worker, maker, channels):
        too_old = await add_item(
            maker, channels[0], "Old", metadata_=dict(PHASE1_DONE),
            fetched_at=utcnow() - timedelta(days=10),
        )
        top = await add_item(maker, channels[0], "Top", metadata_={**PHASE1_DONE, "dedup_phase2": True})
        copy = await add_item(
            maker, channels[0], "Copy", similar_to_id=top, metadata_=dict(PHASE1_DONE)
        )
        new = await add_item(maker, channels[1], "New story", metadata_=dict(PHASE1_DONE))

        classifier = make_classifier([{"id": str(copy), "title": "Copy", "score": 0.93}])
        running_worker._classifier = classifier

        assert await running_worker._process_phase2_dedup() == 1

        item = await get_item(maker, new)
        assert item.similar_to_id == top
        assert item.metadata_["duplicate_method"] == "semantic"
        assert item.metadata_["duplicate_score"] == pytest.approx(0.93)
        assert item.metadata_["dedup_phase2"] is True

        # Lookup by id against every earlier item of the window (copies included)
        classifier.find_duplicates_by_id.assert_awaited_once()
        call = classifier.find_duplicates_by_id.await_args
        assert call.args[0] == new
        assert sorted(call.args[1]) == [top, copy]
        assert too_old not in call.args[1]
        assert call.kwargs["threshold"] == DUPLICATE_THRESHOLD_MAYBE
        # A title-confirmed match can rank below the five best scores
        assert call.kwargs["n_results"] == 20
        classifier.find_duplicates.assert_not_called()

    @pytest.mark.asyncio
    async def test_grey_band_with_similar_titles_links(self, running_worker, maker, channels):
        title = "Vielen Kitas in Hessen fehlen weiterhin Fachkräfte"
        top = await add_item(maker, channels[0], title, metadata_={**PHASE1_DONE, "dedup_phase2": True})
        new = await add_item(
            maker, channels[1], f"Kinderbetreuung: {title}", metadata_=dict(PHASE1_DONE)
        )
        running_worker._classifier = make_classifier(
            [{"id": str(top), "title": title, "score": 0.80}]
        )

        await running_worker._process_phase2_dedup()

        item = await get_item(maker, new)
        assert item.similar_to_id == top
        assert item.metadata_["duplicate_method"] == "semantic_title"
        assert item.metadata_["duplicate_score"] == pytest.approx(0.80)

    @pytest.mark.asyncio
    async def test_grey_band_candidate_only_for_items_waiting_for_llm(
        self, running_worker, maker, channels
    ):
        top = await add_item(
            maker, channels[0], "Landtag beschließt Haushalt",
            metadata_={**PHASE1_DONE, "dedup_phase2": True},
        )
        waiting = await add_item(
            maker, channels[1], "Kritik :an Kürzungen bei Trägern",
            metadata_=dict(PHASE1_DONE), needs_llm_processing=True,
        )
        processed = await add_item(
            maker, channels[1], "Sozialverbände warnen vor Einschnitten",
            metadata_=dict(PHASE1_DONE), needs_llm_processing=False,
        )
        running_worker._classifier = make_classifier(
            [{"id": str(top), "title": "Text :mit Doppelpunkt", "score": 0.80}]
        )

        assert await running_worker._process_phase2_dedup() == 2

        waiting_item = await get_item(maker, waiting)
        assert waiting_item.similar_to_id is None
        assert waiting_item.needs_llm_processing is True
        assert waiting_item.metadata_["duplicate_candidate"]["candidate_id"] == top
        assert waiting_item.metadata_["duplicate_candidate"]["similarity_score"] == pytest.approx(0.80)
        assert waiting_item.metadata_["dedup_phase2"] is True

        processed_item = await get_item(maker, processed)
        assert processed_item.similar_to_id is None
        assert processed_item.needs_llm_processing is False  # never set here
        assert "duplicate_candidate" not in processed_item.metadata_
        assert processed_item.metadata_["dedup_phase2"] is True

    @pytest.mark.asyncio
    async def test_no_match_marks_phase2_done(self, running_worker, maker, channels):
        await add_item(maker, channels[0], "Other", metadata_={**PHASE1_DONE, "dedup_phase2": True})
        new = await add_item(maker, channels[1], "New", metadata_=dict(PHASE1_DONE))
        running_worker._classifier = make_classifier([])

        assert await running_worker._process_phase2_dedup() == 1

        item = await get_item(maker, new)
        assert item.similar_to_id is None
        assert item.metadata_["dedup_phase2"] is True

    @pytest.mark.asyncio
    async def test_not_indexed_item_is_retried_later(self, running_worker, maker, channels):
        """None from the client: phase 2 stays open, no error, later items still run."""
        top = await add_item(maker, channels[0], "Top", metadata_={**PHASE1_DONE, "dedup_phase2": True})
        not_indexed = await add_item(maker, channels[1], "Not indexed", metadata_=dict(PHASE1_DONE))
        indexed = await add_item(maker, channels[1], "Indexed", metadata_=dict(PHASE1_DONE))

        classifier = make_classifier(
            lambda item_id: None
            if item_id == not_indexed
            else [{"id": str(top), "title": "Top", "score": 0.95}]
        )
        running_worker._classifier = classifier

        assert await running_worker._process_phase2_dedup() == 1

        pending = await get_item(maker, not_indexed)
        assert pending.similar_to_id is None
        assert "dedup_phase2" not in pending.metadata_
        assert running_worker._stats["errors"] == 0
        assert running_worker._stats["phase2_checked"] == 1
        assert (await get_item(maker, indexed)).similar_to_id == top

        # Not asked again right away ...
        assert await running_worker._process_phase2_dedup() == 0
        assert classifier.find_duplicates_by_id.await_count == 2

        # ... but once the retry time has passed
        running_worker._phase2_retry_at[not_indexed] = 0.0
        classifier.find_duplicates_by_id.side_effect = None
        classifier.find_duplicates_by_id.return_value = []
        assert await running_worker._process_phase2_dedup() == 1
        assert (await get_item(maker, not_indexed)).metadata_["dedup_phase2"] is True

    @pytest.mark.asyncio
    async def test_better_rated_copy_ends_up_on_top(self, running_worker, maker, channels):
        """A relevant item linked to an irrelevant top item must stay visible."""
        top = await add_item(
            maker, channels[0], "Agenturmeldung", priority=Priority.NONE,
            metadata_={**PHASE1_DONE, "dedup_phase2": True},
        )
        new = await add_item(
            maker, channels[1], "Gleiche Meldung", priority=Priority.HIGH,
            metadata_=dict(PHASE1_DONE),
        )
        running_worker._classifier = make_classifier(
            [{"id": str(top), "title": "Agenturmeldung", "score": 0.95}]
        )

        await running_worker._process_phase2_dedup()

        assert (await get_item(maker, new)).similar_to_id is None
        assert (await get_item(maker, top)).similar_to_id == new

    @pytest.mark.asyncio
    async def test_classifier_unavailable_skips_phase2(self, running_worker, maker, channels):
        await add_item(maker, channels[0], "New", metadata_=dict(PHASE1_DONE))
        running_worker._classifier = None
        with patch(
            "services.relevance_filter.create_relevance_filter",
            new_callable=AsyncMock,
            return_value=None,
        ):
            assert await running_worker._process_phase2_dedup() == 0
