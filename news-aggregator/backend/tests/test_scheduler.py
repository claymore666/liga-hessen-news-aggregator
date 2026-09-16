"""Tests for the scheduler service."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, Source, ConnectorType
from database import utcnow


@pytest.fixture
async def channels_with_intervals(db_session: AsyncSession):
    """Create test channels with different fetch intervals and last_fetch_at."""
    now = utcnow()

    # Create sources first
    source_active = Source(name="Active Source", enabled=True)
    source_disabled = Source(name="Disabled Source", enabled=False)
    db_session.add_all([source_active, source_disabled])
    await db_session.flush()

    # Channel 1: Due (last fetch was 2 hours ago, interval is 60 min)
    channel_due = Channel(
        source_id=source_active.id,
        name="Due Channel",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/due.xml"},
        enabled=True,
        fetch_interval_minutes=60,
        last_fetch_at=now - timedelta(hours=2),
    )

    # Channel 2: Not due (last fetch was 30 min ago, interval is 60 min)
    channel_not_due = Channel(
        source_id=source_active.id,
        name="Not Due Channel",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/not-due.xml"},
        enabled=True,
        fetch_interval_minutes=60,
        last_fetch_at=now - timedelta(minutes=30),
    )

    # Channel 3: Never fetched (NULL last_fetch_at) - always due
    channel_never_fetched = Channel(
        source_id=source_active.id,
        name="Never Fetched Channel",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/never.xml"},
        enabled=True,
        fetch_interval_minutes=60,
        last_fetch_at=None,
    )

    # Channel 4: Disabled channel (should never be fetched)
    channel_disabled = Channel(
        source_id=source_active.id,
        name="Disabled Channel",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/disabled.xml"},
        enabled=False,
        fetch_interval_minutes=60,
        last_fetch_at=now - timedelta(hours=24),
    )

    # Channel 5: Enabled but parent source is disabled
    channel_disabled_source = Channel(
        source_id=source_disabled.id,
        name="Disabled Source Channel",
        connector_type=ConnectorType.RSS,
        config={"url": "https://example.com/disabled-source.xml"},
        enabled=True,
        fetch_interval_minutes=60,
        last_fetch_at=now - timedelta(hours=24),
    )

    db_session.add_all([
        channel_due, channel_not_due, channel_never_fetched,
        channel_disabled, channel_disabled_source
    ])
    await db_session.commit()

    return {
        "due": channel_due,
        "not_due": channel_not_due,
        "never_fetched": channel_never_fetched,
        "disabled": channel_disabled,
        "disabled_source": channel_disabled_source,
    }


class TestFetchDueChannels:
    """Tests for fetch_due_channels function."""

    @pytest.mark.asyncio
    async def test_identifies_due_channels(self, db_session: AsyncSession, channels_with_intervals):
        """Test that due channels are correctly identified."""
        from services.scheduler import fetch_due_channels

        # Mock fetch_channel to track which channels are fetched
        fetched_ids = []

        async def mock_fetch_channel(channel_id, training_mode=False):
            fetched_ids.append(channel_id)
            return 0

        # Patch the database session and fetch_channel
        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", mock_fetch_channel):

            # Setup mock session context manager
            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_due_channels()

        # Should have fetched "due" and "never_fetched" channels
        assert result["due_channels"] == 2
        assert channels_with_intervals["due"].id in fetched_ids
        assert channels_with_intervals["never_fetched"].id in fetched_ids
        assert channels_with_intervals["not_due"].id not in fetched_ids
        assert channels_with_intervals["disabled"].id not in fetched_ids
        assert channels_with_intervals["disabled_source"].id not in fetched_ids

    @pytest.mark.asyncio
    async def test_skips_disabled_channels(self, db_session: AsyncSession, channels_with_intervals):
        """Test that disabled channels are never fetched."""
        from services.scheduler import fetch_due_channels

        fetched_ids = []

        async def mock_fetch_channel(channel_id, training_mode=False):
            fetched_ids.append(channel_id)
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", mock_fetch_channel):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            await fetch_due_channels()

        # Disabled channel and channel with disabled source should never be fetched
        assert channels_with_intervals["disabled"].id not in fetched_ids
        assert channels_with_intervals["disabled_source"].id not in fetched_ids

    @pytest.mark.asyncio
    async def test_null_last_fetch_always_due(self, db_session: AsyncSession, channels_with_intervals):
        """Test that channels with NULL last_fetch_at are always considered due."""
        from services.scheduler import fetch_due_channels

        fetched_ids = []

        async def mock_fetch_channel(channel_id, training_mode=False):
            fetched_ids.append(channel_id)
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", mock_fetch_channel):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            await fetch_due_channels()

        # Never fetched channel should always be due
        assert channels_with_intervals["never_fetched"].id in fetched_ids

    @pytest.mark.asyncio
    async def test_fetch_in_progress_skips(self):
        """Test that concurrent fetches are skipped when lock is held."""
        import services.scheduler as scheduler_module
        from services.scheduler import fetch_due_channels

        # Acquire the lock to simulate a fetch already in progress
        await scheduler_module._fetch_lock.acquire()

        try:
            result = await fetch_due_channels()
            assert result.get("skipped") is True
            assert result.get("reason") == "fetch_in_progress"
        finally:
            scheduler_module._fetch_lock.release()

    @pytest.mark.asyncio
    async def test_handles_fetch_errors(self, db_session: AsyncSession, channels_with_intervals):
        """Test that errors during fetch don't stop other channels."""
        from services.scheduler import fetch_due_channels

        call_count = 0

        async def mock_fetch_channel_with_error(channel_id, training_mode=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Test error")
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", mock_fetch_channel_with_error):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_due_channels()

        # Should have 1 error and 1 success (2 due channels)
        assert result["errors"] == 1
        assert result["fetched"] == 1

    @pytest.mark.asyncio
    async def test_fetches_oldest_first(self, db_session: AsyncSession):
        """Test that channels are fetched oldest first."""
        now = utcnow()

        # Create source
        source = Source(name="Test Source", enabled=True)
        db_session.add(source)
        await db_session.flush()

        # Create channels with different ages
        channel_oldest = Channel(
            source_id=source.id,
            name="Oldest",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/oldest.xml"},
            enabled=True,
            fetch_interval_minutes=60,
            last_fetch_at=now - timedelta(hours=10),
        )
        channel_newer = Channel(
            source_id=source.id,
            name="Newer",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/newer.xml"},
            enabled=True,
            fetch_interval_minutes=60,
            last_fetch_at=now - timedelta(hours=5),
        )

        db_session.add_all([channel_newer, channel_oldest])  # Add in wrong order
        await db_session.commit()

        from services.scheduler import fetch_due_channels

        fetch_order = []

        async def mock_fetch_channel(channel_id, training_mode=False):
            fetch_order.append(channel_id)
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", mock_fetch_channel):

            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            await fetch_due_channels()

        # Oldest should be fetched first
        assert fetch_order[0] == channel_oldest.id
        assert fetch_order[1] == channel_newer.id


# Backward compatibility alias tests
class TestFetchDueSources:
    """Tests to ensure backward compatibility alias works."""

    @pytest.mark.asyncio
    async def test_fetch_due_sources_alias_exists(self):
        """Test that fetch_due_sources is an alias to fetch_due_channels."""
        from services.scheduler import fetch_due_sources, fetch_due_channels
        assert fetch_due_sources is fetch_due_channels


class TestEffectiveLimitPool:
    """Proxy-capped concurrency must count the pool the connector actually uses."""

    def test_x_scraper_is_capped_by_the_https_pool(self):
        """x.com is HTTPS-only, so the HTTP pool says nothing about X capacity."""
        from services.scheduler import get_effective_limit

        with patch("services.proxy_manager.proxy_manager") as pm:
            pm.available_count.return_value = 0
            get_effective_limit("x_scraper")

        pm.available_count.assert_called_once_with("x_scraper", https=True)

    def test_instagram_is_capped_by_the_http_pool(self):
        from services.scheduler import get_effective_limit

        with patch("services.proxy_manager.proxy_manager") as pm:
            pm.available_count.return_value = 0
            get_effective_limit("instagram_scraper")

        pm.available_count.assert_called_once_with("instagram_scraper", https=False)

    def test_empty_pool_still_allows_one_direct_fetch(self):
        from services.scheduler import get_effective_limit

        with patch("services.proxy_manager.proxy_manager") as pm:
            pm.available_count.return_value = 0
            assert get_effective_limit("x_scraper") == 1


# ---------------------------------------------------------------------------
# #187 — a hung cleanup must never wedge the fetch cycle
# ---------------------------------------------------------------------------

def _fake_channel(channel_id: int, connector_type: str = "fake"):
    from unittest.mock import MagicMock

    ch = MagicMock()
    ch.id = channel_id
    ch.connector_type = connector_type
    ch.source.name = f"Source {channel_id}"
    return ch


class TestFetchTimeoutHandling:
    """_fetch_source_type_group cancels on timeout, waits a bounded grace
    period for cleanup and abandons tasks that still do not finish."""

    @pytest.fixture(autouse=True)
    def _reset_breaker(self):
        import services.scheduler as sched

        sched._channel_failures.clear()
        yield
        sched._channel_failures.clear()

    @pytest.mark.asyncio
    async def test_fast_fetch_is_counted(self):
        import asyncio
        from services.scheduler import _fetch_source_type_group

        async def ok_fetch(channel_id, training_mode=False):
            return 1

        with patch("services.scheduler.fetch_channel", ok_fetch), \
             patch.dict("services.scheduler.CHANNEL_FETCH_TIMEOUTS", {"fake": 1.0}):
            result = await _fetch_source_type_group(
                "fake", [_fake_channel(1)], asyncio.Semaphore(2)
            )

        assert (result.fetched, result.errors, result.timeouts, result.abandoned) == (1, 0, 0, 0)
        # Legacy tuple unpacking still works
        fetched, errors = result
        assert (fetched, errors) == (1, 0)

    @pytest.mark.asyncio
    async def test_timeout_cancels_and_waits_for_quick_cleanup(self):
        import asyncio
        from services.scheduler import _fetch_source_type_group

        cleanup_ran = asyncio.Event()

        async def slow_fetch(channel_id, training_mode=False):
            try:
                await asyncio.sleep(60)
            finally:
                await asyncio.sleep(0.05)  # quick cleanup
                cleanup_ran.set()

        with patch("services.scheduler.fetch_channel", slow_fetch), \
             patch.dict("services.scheduler.CHANNEL_FETCH_TIMEOUTS", {"fake": 0.1}), \
             patch("services.scheduler.settings.fetch_cleanup_grace_seconds", 2.0):
            result = await asyncio.wait_for(
                _fetch_source_type_group("fake", [_fake_channel(2)], asyncio.Semaphore(2)),
                timeout=5,
            )

        assert cleanup_ran.is_set()
        assert (result.fetched, result.errors, result.timeouts, result.abandoned) == (0, 1, 1, 0)

    @pytest.mark.asyncio
    async def test_hung_cleanup_is_abandoned_after_grace(self):
        import asyncio
        import services.scheduler as sched
        from services.scheduler import _fetch_source_type_group

        release = asyncio.Event()
        finished = asyncio.Event()

        async def wedged_fetch(channel_id, training_mode=False):
            try:
                await asyncio.sleep(60)
            finally:
                # Simulates Playwright page.close() that Chromium never answers:
                # cleanup ignores the cancellation and blocks indefinitely.
                await release.wait()
                finished.set()

        loop = asyncio.get_running_loop()
        with patch("services.scheduler.fetch_channel", wedged_fetch), \
             patch.dict("services.scheduler.CHANNEL_FETCH_TIMEOUTS", {"fake": 0.1}), \
             patch("services.scheduler.settings.fetch_cleanup_grace_seconds", 0.2):
            t0 = loop.time()
            result = await asyncio.wait_for(
                _fetch_source_type_group("fake", [_fake_channel(3)], asyncio.Semaphore(2)),
                timeout=5,
            )
            elapsed = loop.time() - t0

        # Returned promptly (timeout + grace), not blocked by the wedged task
        assert elapsed < 2.0
        assert (result.timeouts, result.abandoned, result.errors) == (1, 1, 1)
        assert len(sched._abandoned_tasks) == 1
        assert sched._cycle_stats["abandoned_tasks_total"] >= 1

        # When the abandoned task finally completes it is dropped from the set
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        await asyncio.sleep(0)  # let the done-callback run
        assert len(sched._abandoned_tasks) == 0

    @pytest.mark.asyncio
    async def test_cycle_hard_resets_pool_when_task_abandoned(
        self, db_session: AsyncSession, channels_with_intervals
    ):
        """A full fetch_due_channels cycle with one wedged channel completes,
        releases the fetch lock, reports the abandonment and hard-resets the
        browser pool."""
        import asyncio
        import services.scheduler as sched
        from services.scheduler import fetch_due_channels

        release = asyncio.Event()
        calls = 0

        async def fetch(channel_id, training_mode=False):
            nonlocal calls
            calls += 1
            if calls == 1:
                try:
                    await asyncio.sleep(60)
                finally:
                    await release.wait()
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", fetch), \
             patch.dict("services.scheduler.CHANNEL_FETCH_TIMEOUTS", {"rss": 0.1}), \
             patch("services.scheduler.settings.fetch_cleanup_grace_seconds", 0.2), \
             patch("services.browser_pool.browser_pool.hard_reset",
                   new_callable=AsyncMock) as hard_reset, \
             patch("services.scheduler._publish_cycle_stats", new_callable=AsyncMock):
            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await asyncio.wait_for(fetch_due_channels(), timeout=5)

        try:
            assert result["due_channels"] == 2
            assert result["fetched"] == 1
            assert result["abandoned"] == 1
            assert result["timeouts"] == 1
            hard_reset.assert_awaited_once()
            assert "abandoned" in hard_reset.await_args.kwargs["reason"]
            # The lock is free again: the scheduler will run the next cycle
            assert not sched._fetch_lock.locked()
            stats = sched.get_cycle_stats()
            assert stats["last_cycle_abandoned"] == 1
            assert stats["last_cycle_completed_at"] is not None
            assert stats["last_activity_at"] is not None
            assert stats["abandoned_tasks_pending"] == 1
        finally:
            release.set()
            await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_no_hard_reset_without_abandonment(
        self, db_session: AsyncSession, channels_with_intervals
    ):
        from services.scheduler import fetch_due_channels

        async def fetch(channel_id, training_mode=False):
            return 0

        with patch("services.scheduler.async_session_maker") as mock_session_maker, \
             patch("services.scheduler.fetch_channel", fetch), \
             patch("services.browser_pool.browser_pool.hard_reset",
                   new_callable=AsyncMock) as hard_reset, \
             patch("services.scheduler._publish_cycle_stats", new_callable=AsyncMock):
            mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=db_session)
            mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_due_channels()

        assert result["abandoned"] == 0
        hard_reset.assert_not_awaited()


_STALE = {
    "enabled": True, "stale": True, "max_minutes": 15,
    "last_activity_at": "2026-09-14T20:19:00", "age_seconds": 99999, "source": "shared",
}


class TestIngestionFreshness:
    """get_ingestion_freshness() drives /health (#187)."""

    @pytest.mark.asyncio
    async def test_disabled_when_scheduler_disabled(self):
        from services.scheduler import get_ingestion_freshness

        with patch("services.scheduler.settings.scheduler_enabled", False):
            result = await get_ingestion_freshness()
        assert result["enabled"] is False
        assert result["stale"] is False

    @pytest.mark.asyncio
    async def test_disabled_when_threshold_zero(self):
        from services.scheduler import get_ingestion_freshness

        with patch("services.scheduler.settings.scheduler_enabled", True), \
             patch("services.scheduler.settings.scheduler_freshness_max_minutes", 0):
            result = await get_ingestion_freshness()
        assert result["enabled"] is False

    @pytest.mark.asyncio
    async def test_unknown_activity_is_not_stale(self):
        from services.scheduler import get_ingestion_freshness

        with patch("services.scheduler.settings.scheduler_enabled", True), \
             patch("services.scheduler.settings.scheduler_freshness_max_minutes", 15), \
             patch("services.worker_status.read_stats", new_callable=AsyncMock, return_value={}):
            result = await get_ingestion_freshness()
        assert result["enabled"] is True
        assert result["stale"] is False
        assert result["age_seconds"] is None

    @pytest.mark.asyncio
    async def test_old_shared_activity_is_stale(self):
        from services.scheduler import get_ingestion_freshness

        old = (utcnow() - timedelta(minutes=40)).isoformat()
        with patch("services.scheduler.settings.scheduler_enabled", True), \
             patch("services.scheduler.settings.scheduler_freshness_max_minutes", 15), \
             patch("services.worker_status.read_stats", new_callable=AsyncMock,
                   return_value={"cycle": {"last_activity_at": old}}):
            result = await get_ingestion_freshness()
        assert result["stale"] is True
        assert result["source"] == "shared"
        assert result["age_seconds"] > 15 * 60

    @pytest.mark.asyncio
    async def test_recent_shared_activity_is_fresh(self):
        from services.scheduler import get_ingestion_freshness

        recent = (utcnow() - timedelta(minutes=3)).isoformat()
        with patch("services.scheduler.settings.scheduler_enabled", True), \
             patch("services.scheduler.settings.scheduler_freshness_max_minutes", 15), \
             patch("services.worker_status.read_stats", new_callable=AsyncMock,
                   return_value={"cycle": {"last_activity_at": recent}}):
            result = await get_ingestion_freshness()
        assert result["stale"] is False

    @pytest.mark.asyncio
    async def test_stats_read_failure_is_not_stale(self):
        from services.scheduler import get_ingestion_freshness

        with patch("services.scheduler.settings.scheduler_enabled", True), \
             patch("services.scheduler.settings.scheduler_freshness_max_minutes", 15), \
             patch("services.worker_status.read_stats", new_callable=AsyncMock,
                   side_effect=RuntimeError("redis down")):
            result = await get_ingestion_freshness()
        assert result["stale"] is False

    @pytest.mark.asyncio
    async def test_health_endpoint_returns_503_when_stale(self, client):
        with patch("services.scheduler.get_ingestion_freshness",
                   new_callable=AsyncMock, return_value=_STALE):
            response = await client.get("/health")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unhealthy"
        assert body["reason"] == "ingestion_stale"
        assert body["ingestion"]["age_seconds"] == 99999

    @pytest.mark.asyncio
    async def test_admin_health_degraded_when_stale(self, client):
        with patch("services.scheduler.get_ingestion_freshness",
                   new_callable=AsyncMock, return_value=_STALE):
            response = await client.get("/api/admin/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["ingestion"]["stale"] is True


class TestLightConnectorTimeouts:
    def test_link_following_connectors_have_room_for_article_extraction(self):
        """Mastodon/Bluesky follow links by default; 20 s timed out every cycle."""
        from services.scheduler import CHANNEL_FETCH_TIMEOUTS

        for connector in ("mastodon", "bluesky", "telegram"):
            assert CHANNEL_FETCH_TIMEOUTS[connector] >= CHANNEL_FETCH_TIMEOUTS["rss"]


class TestPrefilterCandidates:
    """#189: only entries not yet stored are pre-filtered (each pre-filter call
    embeds the entry through the classifier)."""

    def test_known_urls_are_excluded(self):
        from connectors.base import RawItem
        from services.scheduler import _unknown_raw_items

        items = [
            RawItem(external_id="1", title="a", url="https://example.org/a"),
            RawItem(external_id="2", title="b", url="https://example.org/b"),
        ]
        assert _unknown_raw_items(items, {"https://example.org/a"}) == [items[1]]
        assert _unknown_raw_items(items, set()) == items
