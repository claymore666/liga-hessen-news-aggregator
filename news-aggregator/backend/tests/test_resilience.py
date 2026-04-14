"""Regression tests for resilience fixes.

Tests cover:
- GPU1 timezone handling (#172)
- Redis retry logic (#173)
- DB startup retry with backoff (#175)
- LLM call timeout (#176)
- Digest null safety (#178)
- Exception leakage prevention (#179)
- Browser pool restart and cooldown (#174)

All tests use mocking — no database or external services required.
"""

import asyncio
import time
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# #172 — GPU1 active-hours must use Europe/Berlin, not server-local time
# ---------------------------------------------------------------------------

class TestGPU1Timezone:
    """is_within_active_hours() must use Europe/Berlin regardless of server TZ."""

    def _make_manager(self, start=7, end=16, weekdays_only=True):
        from services.gpu1_power import GPU1PowerManager

        return GPU1PowerManager(
            mac_address="00:11:22:33:44:55",
            ollama_url="http://gpu1:11434",
            active_hours_start=start,
            active_hours_end=end,
            active_weekdays_only=weekdays_only,
        )

    @pytest.mark.asyncio
    async def test_uses_berlin_timezone_not_utc(self):
        """A time that is 10:00 in Berlin but 08:00 UTC must be within hours."""
        from zoneinfo import ZoneInfo

        mgr = self._make_manager(start=7, end=16)
        # Wednesday 10:00 Berlin time
        fake_berlin = datetime(2026, 3, 25, 10, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        mgr._get_force_active = AsyncMock(return_value=False)

        with patch("services.gpu1_power.datetime") as mock_dt:
            # Allow datetime constructor to work normally
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.now.return_value = fake_berlin

            result = await mgr.is_within_active_hours()
            assert result is True

            # Verify datetime.now was called with Europe/Berlin timezone
            call_args = mock_dt.now.call_args
            tz_arg = call_args[0][0]
            assert str(tz_arg) == "Europe/Berlin"

    @pytest.mark.asyncio
    async def test_outside_active_hours(self):
        """22:00 Berlin on a weekday should be outside 7-16 hours."""
        from zoneinfo import ZoneInfo

        mgr = self._make_manager(start=7, end=16)
        fake_berlin = datetime(2026, 3, 25, 22, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        mgr._get_force_active = AsyncMock(return_value=False)

        with patch("services.gpu1_power.datetime") as mock_dt:
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.now.return_value = fake_berlin

            result = await mgr.is_within_active_hours()
            assert result is False

    @pytest.mark.asyncio
    async def test_weekend_blocked_when_weekdays_only(self):
        """Saturday should be blocked when active_weekdays_only=True."""
        from zoneinfo import ZoneInfo

        mgr = self._make_manager(start=7, end=16, weekdays_only=True)
        # Saturday at 10:00 Berlin
        fake_berlin = datetime(2026, 3, 28, 10, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        assert fake_berlin.weekday() == 5  # Sanity check: Saturday
        mgr._get_force_active = AsyncMock(return_value=False)

        with patch("services.gpu1_power.datetime") as mock_dt:
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.now.return_value = fake_berlin

            result = await mgr.is_within_active_hours()
            assert result is False

    @pytest.mark.asyncio
    async def test_force_active_bypasses_hours(self):
        """force_active override should bypass all time checks."""
        mgr = self._make_manager(start=7, end=16)
        mgr._get_force_active = AsyncMock(return_value=True)

        result = await mgr.is_within_active_hours()
        assert result is True

    @pytest.mark.asyncio
    async def test_overnight_range(self):
        """Overnight range (e.g. 22-6) should work correctly."""
        from zoneinfo import ZoneInfo

        mgr = self._make_manager(start=22, end=6, weekdays_only=False)
        fake_berlin = datetime(2026, 3, 25, 23, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        mgr._get_force_active = AsyncMock(return_value=False)

        with patch("services.gpu1_power.datetime") as mock_dt:
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.now.return_value = fake_berlin

            result = await mgr.is_within_active_hours()
            assert result is True


# ---------------------------------------------------------------------------
# Orphan adoption — shutdown_if_idle() handles gpu1 instances we didn't wake
# (e.g., unattended-upgrades reboot at off-hours leaves gpu1 running)
# ---------------------------------------------------------------------------

class TestGPU1OrphanAdoption:
    """shutdown_if_idle() must adopt orphaned gpu1 instances and shut them down."""

    def _make_manager(self, idle_timeout=60):
        from services.gpu1_power import GPU1PowerManager

        return GPU1PowerManager(
            mac_address="00:11:22:33:44:55",
            ollama_url="http://gpu1:11434",
            auto_shutdown=True,
            idle_timeout=idle_timeout,
        )

    @pytest.mark.asyncio
    async def test_adopts_reachable_orphan(self):
        """First call adopts a reachable orphan — sets _was_sleeping, fresh idle clock."""
        mgr = self._make_manager()
        mgr.is_available = AsyncMock(return_value=True)

        result = await mgr.shutdown_if_idle()

        assert result is False  # Don't shutdown immediately on adoption
        assert mgr._was_sleeping is True
        assert mgr._last_activity is not None
        assert mgr.get_idle_time() < 1.0  # Just adopted, no idle time yet

    @pytest.mark.asyncio
    async def test_does_not_adopt_unreachable_orphan(self):
        """If gpu1 is down, no adoption — nothing to shutdown."""
        mgr = self._make_manager()
        mgr.is_available = AsyncMock(return_value=False)

        result = await mgr.shutdown_if_idle()

        assert result is False
        assert mgr._was_sleeping is False  # Not adopted

    @pytest.mark.asyncio
    async def test_adopted_gpu1_shuts_down_after_idle_timeout(self):
        """After adoption + idle_timeout passes, shutdown should fire."""
        mgr = self._make_manager(idle_timeout=60)

        # First call — adopts
        mgr.is_available = AsyncMock(return_value=True)
        await mgr.shutdown_if_idle()
        assert mgr._was_sleeping is True

        # Backdate last_activity to simulate idle period elapsed
        mgr._last_activity = time.time() - 120
        mgr.has_other_users = AsyncMock(return_value=False)
        mgr.shutdown = AsyncMock(return_value=True)
        mgr.clear_force_active = AsyncMock()

        result = await mgr.shutdown_if_idle()

        assert result is True
        mgr.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_other_users_block_shutdown_after_adoption(self):
        """A logged-in user (kamienc on console) must block shutdown."""
        mgr = self._make_manager(idle_timeout=60)

        # Adopt
        mgr.is_available = AsyncMock(return_value=True)
        await mgr.shutdown_if_idle()

        # Idle elapsed, but a user is logged in
        mgr._last_activity = time.time() - 120
        mgr.has_other_users = AsyncMock(return_value=True)
        mgr.shutdown = AsyncMock(return_value=True)

        result = await mgr.shutdown_if_idle()

        assert result is False
        mgr.shutdown.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_shutdown_disabled_skips_adoption(self):
        """If auto_shutdown is off, shutdown_if_idle short-circuits before adoption."""
        mgr = self._make_manager()
        mgr.auto_shutdown = False
        mgr.is_available = AsyncMock(return_value=True)

        result = await mgr.shutdown_if_idle()

        assert result is False
        assert mgr._was_sleeping is False


# ---------------------------------------------------------------------------
# #173 — Redis retry after 60s when _available=False
# ---------------------------------------------------------------------------

class TestRedisRetry:
    """get_redis() should retry after _RETRY_INTERVAL when previously failed."""

    def _save_state(self, rc):
        return rc._redis, rc._available, rc._last_retry_at

    def _restore_state(self, rc, state):
        rc._redis, rc._available, rc._last_retry_at = state

    @pytest.mark.asyncio
    async def test_retries_after_interval(self):
        """When _available=False and 60s have passed, init_redis should be called."""
        import services.redis_client as rc

        state = self._save_state(rc)
        try:
            rc._redis = None
            rc._available = False
            # Pretend last retry was 61 seconds ago
            rc._last_retry_at = time.monotonic() - 61.0

            with patch("services.redis_client.init_redis", new_callable=AsyncMock) as mock_init:
                await rc.get_redis()
                mock_init.assert_called_once()
        finally:
            self._restore_state(rc, state)

    @pytest.mark.asyncio
    async def test_no_retry_before_interval(self):
        """When _available=False and <60s, init_redis should NOT be called."""
        import services.redis_client as rc

        state = self._save_state(rc)
        try:
            rc._redis = None
            rc._available = False
            # Pretend last retry was only 10 seconds ago
            rc._last_retry_at = time.monotonic() - 10.0

            with patch("services.redis_client.init_redis", new_callable=AsyncMock) as mock_init:
                result = await rc.get_redis()
                mock_init.assert_not_called()
                assert result is None
        finally:
            self._restore_state(rc, state)

    @pytest.mark.asyncio
    async def test_first_call_initializes(self):
        """First call (_available=None) should call init_redis."""
        import services.redis_client as rc

        state = self._save_state(rc)
        try:
            rc._redis = None
            rc._available = None  # Never tested

            with patch("services.redis_client.init_redis", new_callable=AsyncMock) as mock_init:
                await rc.get_redis()
                mock_init.assert_called_once()
        finally:
            self._restore_state(rc, state)

    @pytest.mark.asyncio
    async def test_returns_existing_client(self):
        """When already connected, should return the existing client."""
        import services.redis_client as rc

        state = self._save_state(rc)
        try:
            mock_client = MagicMock()
            rc._redis = mock_client
            rc._available = True

            result = await rc.get_redis()
            assert result is mock_client
        finally:
            self._restore_state(rc, state)


# ---------------------------------------------------------------------------
# #175 — DB startup retry with exponential backoff
# ---------------------------------------------------------------------------

class TestDBStartupRetry:
    """init_db() failure should be retried by the startup sequence."""

    @pytest.mark.asyncio
    async def test_init_db_propagates_errors(self):
        """init_db should propagate connection errors so caller can retry."""
        with patch("database.engine") as mock_engine:
            mock_engine.begin.side_effect = ConnectionError("Connection refused")

            from database import init_db

            with pytest.raises(ConnectionError, match="Connection refused"):
                await init_db()

    @pytest.mark.asyncio
    async def test_startup_retries_init_db(self):
        """Simulates the startup retry pattern: retry init_db with backoff."""
        call_count = 0

        async def flaky_init_db():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection refused")

        # Simulate the retry loop pattern used in main.py startup
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                await flaky_init_db()
                break
            except ConnectionError:
                if attempt == max_retries:
                    pytest.fail("Should have succeeded before max retries")
                # In production this would be asyncio.sleep(2 ** attempt)
                continue

        assert call_count == 3  # Failed twice, succeeded on third

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self):
        """Should give up and raise after max retries exhausted."""

        async def always_fail():
            raise ConnectionError("Connection refused")

        max_retries = 3
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                await always_fail()
                break
            except ConnectionError as e:
                last_error = e
                if attempt == max_retries:
                    break

        assert last_error is not None
        assert "Connection refused" in str(last_error)


# ---------------------------------------------------------------------------
# #176 — LLM call timeout (120s)
# ---------------------------------------------------------------------------

class TestLLMCallTimeout:
    """LLM calls in processor.py must use LLM_CALL_TIMEOUT."""

    def test_timeout_constant_is_120(self):
        """LLM_CALL_TIMEOUT should be 120 seconds."""
        from services.processor import LLM_CALL_TIMEOUT

        assert LLM_CALL_TIMEOUT == 120

    @pytest.mark.asyncio
    async def test_analyze_returns_default_on_timeout(self):
        """analyze() should return default analysis when LLM times out."""
        from services.processor import ItemProcessor
        from services.llm import LLMService

        mock_llm = MagicMock(spec=LLMService)
        # Make complete() hang forever — wait_for will time out
        mock_llm.complete = AsyncMock(side_effect=asyncio.TimeoutError())

        processor = ItemProcessor(llm_service=mock_llm)

        item = MagicMock()
        item.title = "Test"
        item.content = "Content"
        item.source = None
        item.published_at = None

        # Patch wait_for to immediately raise TimeoutError
        with patch("services.processor.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await processor.analyze(item)

        # Should return default (irrelevant) analysis, not raise
        assert result["relevant"] is False
        assert result["priority"] is None
        assert result["relevance_score"] == 0.0

    @pytest.mark.asyncio
    async def test_summarize_returns_none_on_timeout(self):
        """summarize() should return None on timeout, not raise."""
        from services.processor import ItemProcessor

        mock_llm = MagicMock()
        processor = ItemProcessor(llm_service=mock_llm)

        item = MagicMock()
        item.title = "Test"
        item.content = "Content"

        with patch("services.processor.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await processor.summarize(item)

        assert result is None

    @pytest.mark.asyncio
    async def test_confirm_duplicate_returns_false_on_timeout(self):
        """confirm_duplicate() should return (False, reason) on timeout."""
        from services.processor import ItemProcessor

        mock_llm = MagicMock()
        processor = ItemProcessor(llm_service=mock_llm)

        with patch("services.processor.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            is_dup, reason = await processor.confirm_duplicate(
                MagicMock(title="A", content="C1"),
                MagicMock(title="B", content="C2"),
            )

        assert is_dup is False
        assert "Timeout" in reason

    @pytest.mark.asyncio
    async def test_wait_for_called_with_correct_timeout(self):
        """asyncio.wait_for should be called with LLM_CALL_TIMEOUT=120."""
        from services.processor import ItemProcessor, LLM_CALL_TIMEOUT
        from services.llm import LLMResponse

        mock_llm = MagicMock()
        processor = ItemProcessor(llm_service=mock_llm)

        item = MagicMock()
        item.title = "Test"
        item.content = "Content"
        item.source = None
        item.published_at = None

        mock_response = LLMResponse(
            text='{"summary": "Test", "relevant": false, "priority": null}',
            model="test",
            provider="test",
        )

        with patch("services.processor.asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = mock_response
            await processor.analyze(item)

            # Verify timeout parameter
            _, kwargs = mock_wait.call_args
            assert kwargs["timeout"] == LLM_CALL_TIMEOUT
            assert kwargs["timeout"] == 120


# ---------------------------------------------------------------------------
# #178 — Digest null safety
# ---------------------------------------------------------------------------

class TestDigestNullSafety:
    """render_digest_html/text must handle None values without crashing."""

    def test_html_with_all_none_fields(self):
        """HTML render should handle content with all None optional fields."""
        from services.digest_email import render_digest_html

        content = {
            "editorial_intro": None,
            "urgent": None,
            "top_stories": None,
            "further_news": None,
        }
        result = render_digest_html(content, date(2026, 3, 26), 0)
        assert "Tagesüberblick" in result
        assert "<!DOCTYPE html>" in result

    def test_text_with_all_none_fields(self):
        """Text render should handle content with all None optional fields."""
        from services.digest_email import render_digest_text

        content = {
            "editorial_intro": None,
            "urgent": None,
            "top_stories": None,
            "further_news": None,
        }
        result = render_digest_text(content, date(2026, 3, 26), 0)
        assert "TAGESÜBERBLICK" in result

    def test_html_with_empty_content(self):
        """HTML render should handle completely empty content dict."""
        from services.digest_email import render_digest_html

        content = {}
        result = render_digest_html(content, date(2026, 3, 26), 0)
        assert "Tagesüberblick" in result

    def test_text_with_empty_content(self):
        """Text render should handle completely empty content dict."""
        from services.digest_email import render_digest_text

        content = {}
        result = render_digest_text(content, date(2026, 3, 26), 0)
        assert "TAGESÜBERBLICK" in result

    def test_html_with_none_entry_fields(self):
        """HTML render handles entries where headline/context/url/source are None."""
        from services.digest_email import render_digest_html

        content = {
            "editorial_intro": "Intro",
            "urgent": [{"headline": None, "context": None, "url": None, "source": None, "assigned_aks": None}],
            "top_stories": [{"headline": None, "context": None, "url": None, "source": None, "assigned_aks": None}],
            "further_news": [{"headline": None, "url": None}],
        }
        result = render_digest_html(content, date(2026, 3, 26), 3)
        assert "<!DOCTYPE html>" in result

    def test_text_with_none_entry_fields(self):
        """Text render handles entries where fields are None."""
        from services.digest_email import render_digest_text

        content = {
            "editorial_intro": "Intro",
            "urgent": [{"headline": None, "context": None, "source": None, "url": None}],
            "top_stories": [{"headline": None, "context": None, "source": None, "url": None}],
            "further_news": [{"headline": None, "url": None}],
        }
        result = render_digest_text(content, date(2026, 3, 26), 3)
        assert "TAGESÜBERBLICK" in result

    def test_html_with_missing_keys(self):
        """HTML render handles entries that are missing expected keys entirely."""
        from services.digest_email import render_digest_html

        content = {
            "editorial_intro": "Intro",
            "urgent": [{}],  # No keys at all
            "top_stories": [{}],
            "further_news": [{}],
        }
        result = render_digest_html(content, date(2026, 3, 26), 3)
        assert "<!DOCTYPE html>" in result

    def test_text_with_missing_keys(self):
        """Text render handles entries that are missing expected keys entirely."""
        from services.digest_email import render_digest_text

        content = {
            "editorial_intro": "Intro",
            "urgent": [{}],
            "top_stories": [{}],
            "further_news": [{}],
        }
        result = render_digest_text(content, date(2026, 3, 26), 3)
        assert "TAGESÜBERBLICK" in result

    def test_html_normal_content(self):
        """HTML render works correctly with fully populated content."""
        from services.digest_email import render_digest_html

        content = {
            "editorial_intro": "Test editorial",
            "urgent": [{
                "headline": "Urgent headline",
                "context": "Urgent context",
                "url": "https://example.com/1",
                "source": "Test Source",
                "assigned_aks": ["AK1"],
            }],
            "top_stories": [{
                "headline": "Top headline",
                "context": "Top context",
                "url": "https://example.com/2",
                "source": "Test Source",
                "assigned_aks": ["AK3", "AK5"],
            }],
            "further_news": [{
                "headline": "Further headline",
                "url": "https://example.com/3",
            }],
        }
        result = render_digest_html(content, date(2026, 3, 26), 10)
        assert "Urgent headline" in result
        assert "Top headline" in result
        assert "Further headline" in result
        assert "Test editorial" in result


# ---------------------------------------------------------------------------
# #179 — Exception leakage prevention
# ---------------------------------------------------------------------------

class TestExceptionLeakage:
    """Global exception handler must return generic 500, not stack traces."""

    @pytest.mark.asyncio
    async def test_handler_returns_500(self):
        """Handler should return status 500 with generic message."""
        from main import global_exception_handler

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/test"

        exc = ValueError("sensitive database connection string here")

        response = await global_exception_handler(request, exc)

        assert response.status_code == 500
        # Decode the body to check content
        body = response.body.decode()
        assert "Internal server error" in body

    @pytest.mark.asyncio
    async def test_handler_does_not_leak_exception_details(self):
        """Response body must not contain the exception message."""
        from main import global_exception_handler

        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/items"

        secret_msg = "password=hunter2&host=db.internal"
        exc = RuntimeError(secret_msg)

        response = await global_exception_handler(request, exc)

        body = response.body.decode()
        assert secret_msg not in body
        assert "hunter2" not in body
        assert "Traceback" not in body

    @pytest.mark.asyncio
    async def test_handler_returns_json(self):
        """Response should be valid JSON with 'detail' key."""
        import json as json_mod
        from main import global_exception_handler

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/test"

        response = await global_exception_handler(request, Exception("boom"))

        parsed = json_mod.loads(response.body)
        assert "detail" in parsed
        assert parsed["detail"] == "Internal server error"


# ---------------------------------------------------------------------------
# #174 — Browser pool restart and cooldown
# ---------------------------------------------------------------------------

class TestBrowserPoolResilience:
    """Browser pool handles cooldown and restart failures correctly."""

    def _make_pool(self):
        from services.browser_pool import BrowserPool

        return BrowserPool(max_browsers=2, error_threshold=3)

    @pytest.mark.asyncio
    async def test_cooldown_prevents_rapid_restarts(self):
        """After MAX_RESTART_FAILURES, pool should enter cooldown."""
        pool = self._make_pool()

        # Simulate consecutive restart failures
        pool._consecutive_restart_failures = pool.MAX_RESTART_FAILURES
        pool._last_restart_attempt = time.monotonic()  # Just now

        # Trying to initialize should raise RuntimeError about cooldown
        with pytest.raises(RuntimeError, match="cooldown"):
            await pool._ensure_initialized()

    @pytest.mark.asyncio
    async def test_cooldown_expires(self):
        """After cooldown expires, pool should allow reinitialization."""
        pool = self._make_pool()

        pool._consecutive_restart_failures = pool.MAX_RESTART_FAILURES
        # Pretend last attempt was long ago
        pool._last_restart_attempt = time.monotonic() - pool.RESTART_COOLDOWN - 1

        # Should attempt to initialize (will fail since no real Playwright,
        # but should NOT raise cooldown error)
        with patch("services.browser_pool.async_playwright") as mock_pw:
            mock_start = AsyncMock()
            mock_pw.return_value.start = mock_start
            mock_start.return_value = MagicMock()

            playwright = await pool._ensure_initialized()
            assert playwright is not None
            assert pool._consecutive_restart_failures == 0

    @pytest.mark.asyncio
    async def test_generation_counter_prevents_redundant_restarts(self):
        """Restart should be skipped if generation already advanced."""
        pool = self._make_pool()
        pool._generation = 5
        pool._playwright = MagicMock()  # Pretend we have a running instance

        # Try to restart with a stale generation
        await pool._restart_driver(trigger_generation=3)

        # Should not have restarted — generation already advanced
        assert pool._generation == 5

    @pytest.mark.asyncio
    async def test_restart_failure_increments_counter(self):
        """Failed restart should increment consecutive_restart_failures."""
        pool = self._make_pool()
        pool._generation = 1
        pool._playwright = MagicMock()
        pool._playwright.stop = AsyncMock()

        with patch("services.browser_pool.async_playwright") as mock_pw:
            mock_pw.return_value.start = AsyncMock(side_effect=OSError("spawn failed"))

            await pool._restart_driver(trigger_generation=1)

            assert pool._consecutive_restart_failures == 1
            assert pool._playwright is None

    @pytest.mark.asyncio
    async def test_restart_cleans_up_old_playwright_on_failure(self):
        """On restart failure, old Playwright stop() should be called for cleanup."""
        pool = self._make_pool()
        pool._generation = 1

        old_pw = MagicMock()
        old_pw.stop = AsyncMock()
        pool._playwright = old_pw

        with patch("services.browser_pool.async_playwright") as mock_pw:
            mock_pw.return_value.start = AsyncMock(side_effect=OSError("spawn failed"))

            await pool._restart_driver(trigger_generation=1)

            # old_pw.stop should have been called (once during normal stop,
            # and potentially again during cleanup)
            assert old_pw.stop.call_count >= 1

    @pytest.mark.asyncio
    async def test_error_count_triggers_restart(self):
        """Reaching error_threshold should trigger restart."""
        pool = self._make_pool()
        pool._generation = 1
        pool._error_count = pool._error_threshold - 1  # One below threshold

        with patch.object(pool, "_restart_driver", new_callable=AsyncMock) as mock_restart:
            await pool._handle_error(error_generation=1)
            mock_restart.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_stale_error_ignored(self):
        """Error from old generation should not trigger restart."""
        pool = self._make_pool()
        pool._generation = 5
        pool._error_count = 100  # High count but stale

        with patch.object(pool, "_restart_driver", new_callable=AsyncMock) as mock_restart:
            await pool._handle_error(error_generation=3)  # Old generation
            mock_restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_prevents_new_browsers(self):
        """After shutdown, get_browser should raise."""
        pool = self._make_pool()
        pool._shutting_down = True

        with pytest.raises(RuntimeError, match="shutting down"):
            async with pool.get_browser():
                pass
