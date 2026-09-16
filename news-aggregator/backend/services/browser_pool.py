"""
Shared browser pool for Playwright-based scrapers.

This module provides a singleton Playwright instance to avoid spawning
multiple node driver processes. Each driver process consumes significant
resources, and without pooling they can accumulate and cause
"Resource temporarily unavailable" (Errno 11) errors.

Usage:
    from services.browser_pool import browser_pool, close_quietly

    async with browser_pool.get_browser() as browser:
        context = await browser.new_context(...)
        try:
            page = await context.new_page()
            # ... use page ...
        finally:
            await close_quietly(context, "context")

Cleanup rules (see issue #187):

* Playwright sends ``page.close()`` / ``context.close()`` / ``browser.close()``
  with an *infinite* protocol timeout, and a Chromium page that refuses to
  close never answers. Always close through :func:`close_quietly`, which
  waits a bounded time and then abandons the call.
* Never ``cancel()`` a coroutine that is inside a Playwright call: the
  client reacts to ``CancelledError`` by awaiting the pending reply without
  a timeout. :func:`close_quietly` runs the close in its own task and only
  waits on that task, so cancelling the *caller* is safe.
* When cleanup still hangs, :meth:`BrowserPool.hard_reset` SIGKILLs the node
  driver. That closes the transport, every pending protocol call fails with
  "Connection closed" immediately, and Chromium dies with its parent.
"""

import asyncio
import logging
import os
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import async_playwright, Browser, Playwright

logger = logging.getLogger(__name__)


def _default_close_timeout() -> float:
    try:
        from config import settings

        return float(settings.browser_close_timeout_seconds)
    except Exception:  # pragma: no cover - config import problems at startup
        return float(os.environ.get("BROWSER_CLOSE_TIMEOUT_SECONDS", "10"))


def _default_max_browsers() -> int:
    try:
        from config import settings

        return int(settings.browser_pool_max)
    except Exception:  # pragma: no cover
        return int(os.environ.get("BROWSER_POOL_MAX", "2"))


def _swallow_task_result(task: asyncio.Task) -> None:
    """Done-callback for abandoned tasks: retrieve the exception so asyncio
    does not log "Task exception was never retrieved"."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.debug("Abandoned close finished with: %r", exc)


async def close_quietly(
    obj,
    what: str = "resource",
    timeout: float | None = None,
) -> bool:
    """Close a Playwright page/context/browser with a bounded wait.

    Never raises. Returns True when the close completed (successfully or
    with an error) within ``timeout`` seconds, False when it was abandoned.

    The close runs in its own task so that:
    * a wedged Chromium page cannot block the caller for longer than
      ``timeout``;
    * cancelling the caller (scheduler timeout) lands in ``asyncio.wait``
      rather than inside the Playwright call, which would otherwise wait
      for the reply without a timeout.

    An abandoned close is left pending; it resolves once the driver answers
    or once :meth:`BrowserPool.hard_reset` closes the connection.
    """
    if obj is None:
        return True
    if timeout is None:
        timeout = _default_close_timeout()

    try:
        task = asyncio.ensure_future(obj.close())
    except Exception as e:  # e.g. close() on an already-closed object
        logger.debug("Error starting close of %s: %s", what, e)
        return True

    task.add_done_callback(_swallow_task_result)
    try:
        done, pending = await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        # Caller cancelled while we waited: leave the close running detached.
        raise
    if pending:
        logger.warning(
            "Closing %s did not finish within %.0fs; abandoning the close",
            what,
            timeout,
        )
        return False
    if not task.cancelled() and task.exception() is not None:
        logger.debug("Error closing %s: %s", what, task.exception())
    return True


class BrowserPool:
    """
    Singleton browser pool using a shared Playwright instance.

    Instead of each scraper creating its own Playwright driver (node process),
    this pool maintains a single driver and creates/recycles browsers from it.

    Restart logic uses a generation counter to prevent concurrent callers from
    triggering redundant restarts, and a cooldown to avoid restart storms.
    """

    RESTART_COOLDOWN = 30.0  # seconds between restart attempts
    MAX_RESTART_FAILURES = 3  # consecutive failures before giving up until cooldown
    START_TIMEOUT = 60.0  # seconds for async_playwright().start()
    LOCK_TIMEOUT = 15.0  # how long hard_reset waits for the pool lock

    def __init__(
        self,
        max_browsers: int | None = None,
        error_threshold: int = 10,
        close_timeout: float | None = None,
    ):
        self._max_browsers = max_browsers if max_browsers is not None else _default_max_browsers()
        self._playwright: Playwright | None = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self._max_browsers)
        self._initialized = False
        self._shutting_down = False
        self._error_count = 0
        self._error_threshold = error_threshold
        self._success_count = 0
        self._generation = 0  # incremented on each restart
        self._last_restart_attempt = 0.0
        self._consecutive_restart_failures = 0
        self._close_timeout = close_timeout
        self._hung_closes = 0
        self._hard_resets = 0
        self._last_hard_reset_at: float | None = None
        self._last_hard_reset_reason: str | None = None

    @property
    def close_timeout(self) -> float:
        if self._close_timeout is None:
            self._close_timeout = _default_close_timeout()
        return self._close_timeout

    # ------------------------------------------------------------------
    # Driver process helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _driver_process(playwright: Playwright | None):
        """Return the node driver subprocess behind a Playwright instance, if reachable."""
        if playwright is None:
            return None
        try:
            return playwright._impl_obj._connection._transport._proc  # type: ignore[attr-defined]
        except AttributeError:
            return None

    @classmethod
    def _driver_pid(cls, playwright: Playwright | None) -> int | None:
        proc = cls._driver_process(playwright)
        if proc is None:
            return None
        return getattr(proc, "pid", None)

    @classmethod
    def _kill_driver(cls, playwright: Playwright | None) -> int | None:
        """SIGKILL the node driver. Returns the pid that was signalled, or None."""
        proc = cls._driver_process(playwright)
        if proc is None:
            return None
        pid = getattr(proc, "pid", None)
        if pid is None or getattr(proc, "returncode", None) is not None:
            return None
        try:
            proc.kill()
            logger.warning("Killed Playwright driver process (pid %s)", pid)
        except ProcessLookupError:
            return None
        except Exception as e:
            logger.warning("Failed to kill Playwright driver pid %s: %s", pid, e)
            return None
        return pid

    @staticmethod
    def _reap_orphan_chromium() -> int:
        """Kill headless Chromium processes that lost their parent.

        Chromium normally dies with the driver, but a renderer that was stuck
        in a syscall can survive and get re-parented to PID 1. Only processes
        whose parent is PID 1 and whose command line marks them as a headless
        Playwright Chromium are touched. Linux-only; returns the number killed.
        """
        proc_root = Path("/proc")
        if not proc_root.exists():
            return 0
        my_uid = os.getuid()
        killed = 0
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid in (1, os.getpid()):
                continue
            try:
                if entry.stat().st_uid != my_uid:
                    continue
                status = (entry / "status").read_text()
                ppid = 0
                for line in status.splitlines():
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                        break
                if ppid != 1:
                    continue
                raw_cmdline = (entry / "cmdline").read_bytes()
                cmdline = raw_cmdline.replace(b"\0", b" ").decode(errors="replace")
            except (OSError, ValueError):
                continue
            if "--headless" not in cmdline:
                continue
            if "chrom" not in cmdline.lower():
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except OSError:
                continue
        if killed:
            logger.warning("Killed %d orphaned headless Chromium process(es)", killed)
        return killed

    async def _stop_playwright(self, playwright: Playwright | None, timeout: float) -> None:
        """Stop a Playwright instance, killing its driver when stop() hangs."""
        if playwright is None:
            return
        try:
            task = asyncio.ensure_future(playwright.stop())
        except Exception as e:
            logger.debug("Error starting Playwright stop: %s", e)
            self._kill_driver(playwright)
            return
        task.add_done_callback(_swallow_task_result)
        done, pending = await asyncio.wait({task}, timeout=timeout)
        if pending:
            logger.warning("Playwright stop() did not finish within %.0fs; killing driver", timeout)
            self._kill_driver(playwright)
            await asyncio.wait({task}, timeout=5.0)
        elif not task.cancelled() and task.exception() is not None:
            logger.debug("Error stopping Playwright: %s", task.exception())

    async def _start_playwright(self) -> Playwright:
        return await asyncio.wait_for(async_playwright().start(), timeout=self.START_TIMEOUT)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _ensure_initialized(self) -> Playwright:
        """Ensure Playwright is initialized, creating it if needed."""
        if self._playwright is not None:
            return self._playwright

        async with self._lock:
            if self._playwright is not None:
                return self._playwright

            if self._shutting_down:
                raise RuntimeError("Browser pool is shutting down")

            # If we're in cooldown after consecutive restart failures, check
            # whether the cooldown has expired. If it has, reset the counter
            # so the initialization attempt can proceed. If it hasn't, raise
            # rather than returning None or retrying too aggressively.
            if self._consecutive_restart_failures >= self.MAX_RESTART_FAILURES:
                elapsed = time.monotonic() - self._last_restart_attempt
                if elapsed < self.RESTART_COOLDOWN:
                    raise RuntimeError(
                        f"Browser pool in cooldown after {self._consecutive_restart_failures} "
                        f"restart failures ({self.RESTART_COOLDOWN - elapsed:.0f}s remaining)"
                    )
                logger.info(
                    "Cooldown expired after %d consecutive failures, attempting reinitialization",
                    self._consecutive_restart_failures,
                )
                self._consecutive_restart_failures = 0

            logger.info("Initializing shared Playwright instance...")
            try:
                self._playwright = await self._start_playwright()
            except Exception:
                self._consecutive_restart_failures += 1
                self._last_restart_attempt = time.monotonic()
                raise
            self._initialized = True
            self._generation += 1
            self._error_count = 0
            self._consecutive_restart_failures = 0
            logger.info(
                "Playwright instance ready (generation %d, driver pid %s)",
                self._generation,
                self._driver_pid(self._playwright),
            )
            return self._playwright

    @asynccontextmanager
    async def get_browser(
        self,
        headless: bool = True,
        args: list[str] | None = None,
        slot_timeout: float = 120.0,
    ):
        """Acquire a pool slot and launch a browser.

        ``slot_timeout`` bounds the wait for a free slot. Opportunistic users
        (the article extractor's SPA fallback inside a 90 s channel budget)
        pass a few seconds so they fail fast instead of queueing behind the
        social-media scrapers that hold slots for minutes.
        """
        if self._shutting_down:
            raise RuntimeError("Browser pool is shutting down")

        browser: Browser | None = None

        # Hold on to the semaphore object: hard_reset() swaps in a fresh one,
        # and an abandoned task must release the slot it acquired, not a slot
        # of the new pool.
        semaphore = self._semaphore
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=slot_timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Browser pool: timed out waiting for available slot ({slot_timeout:.0f}s)"
            )

        try:
            # Capture generation before we start so we can detect stale errors
            gen_before = self._generation

            try:
                playwright = await self._ensure_initialized()

                launch_args = args or [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ]

                browser = await asyncio.wait_for(
                    playwright.chromium.launch(
                        headless=headless,
                        args=launch_args,
                    ),
                    timeout=30.0,
                )

                yield browser

                # Success — reset error count
                self._error_count = 0
                self._success_count += 1

            except asyncio.TimeoutError:
                logger.error("Browser launch timeout")
                await self._handle_error(gen_before)
                raise
            except asyncio.CancelledError:
                # Caller was cancelled (scheduler timeout). Not a pool error;
                # cleanup below is bounded so the cancellation can complete.
                raise
            except Exception as e:
                logger.error("Browser error: %s", e)
                await self._handle_error(gen_before)
                raise
            finally:
                if browser:
                    closed = await close_quietly(browser, "browser", self.close_timeout)
                    if not closed:
                        self._hung_closes += 1
                        # A browser that will not close leaks a Chromium
                        # process; count it towards a driver restart.
                        await self._handle_error(gen_before)
        finally:
            semaphore.release()

    async def _handle_error(self, error_generation: int):
        """Handle browser error. Only trigger restart if still on the same generation."""
        # If a restart already happened since our request started, skip
        if error_generation != self._generation:
            return

        self._error_count += 1
        if self._error_count >= self._error_threshold:
            await self._restart_driver(error_generation)

    async def _restart_driver(self, trigger_generation: int):
        """
        Restart the Playwright driver.

        Uses generation tracking to ensure only one restart per failure episode,
        and a cooldown to prevent restart storms.
        """
        async with self._lock:
            # Another caller already restarted — nothing to do
            if self._generation != trigger_generation:
                return

            # Cooldown: don't restart too frequently
            now = time.monotonic()
            elapsed = now - self._last_restart_attempt
            if elapsed < self.RESTART_COOLDOWN and self._consecutive_restart_failures > 0:
                logger.debug(
                    "Restart cooldown active (%.0fs remaining), skipping",
                    self.RESTART_COOLDOWN - elapsed,
                )
                return

            # Give up after too many consecutive failures until cooldown expires
            if self._consecutive_restart_failures >= self.MAX_RESTART_FAILURES:
                if elapsed < self.RESTART_COOLDOWN:
                    return
                # Cooldown expired, allow retry
                logger.info(
                    "Cooldown expired after %d consecutive restart failures, retrying",
                    self._consecutive_restart_failures,
                )
                self._consecutive_restart_failures = 0

            self._last_restart_attempt = now

            logger.warning(
                "Restarting Playwright driver (generation %d, %d errors)...",
                self._generation,
                self._error_count,
            )

            # Save old reference so we can clean up properly on failure
            old_playwright = self._playwright

            # Stop existing driver (bounded; kills the process if stop hangs)
            await self._stop_playwright(old_playwright, self.close_timeout)

            # Reinitialize
            try:
                self._playwright = await self._start_playwright()
                self._initialized = True
                self._generation += 1
                self._error_count = 0
                self._consecutive_restart_failures = 0
                logger.info(
                    "Playwright driver restarted successfully (generation %d)",
                    self._generation,
                )
            except Exception as e:
                self._playwright = None
                self._initialized = False
                self._consecutive_restart_failures += 1
                # Make sure the old driver is really gone
                self._kill_driver(old_playwright)
                logger.error(
                    "Failed to restart Playwright driver (attempt %d/%d): %s",
                    self._consecutive_restart_failures,
                    self.MAX_RESTART_FAILURES,
                    e,
                )

    async def hard_reset(self, reason: str = "unspecified") -> dict:
        """Forcefully discard the driver and every browser it owns.

        Used when a fetch task had to be abandoned mid-cleanup: the abandoned
        task may hold a pool slot and an unanswered ``close()`` call. Killing
        the driver fails every pending protocol call immediately, Chromium
        exits with its parent, and a fresh semaphore restores the pool's
        capacity. The next ``get_browser()`` call starts a new driver.

        Returns a small summary dict for logging/stats.
        """
        self._hard_resets += 1
        self._last_hard_reset_at = time.time()
        self._last_hard_reset_reason = reason
        logger.warning("Hard-resetting browser pool: %s", reason)

        # Do not let a wedged _ensure_initialized/_restart_driver block the reset.
        locked = False
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=self.LOCK_TIMEOUT)
            locked = True
        except asyncio.TimeoutError:
            logger.error(
                "Browser pool lock not acquired within %.0fs; resetting without it",
                self.LOCK_TIMEOUT,
            )

        try:
            old_playwright = self._playwright
            self._playwright = None
            self._initialized = False
            self._generation += 1
            self._error_count = 0
            self._consecutive_restart_failures = 0
            self._last_restart_attempt = time.monotonic()

            # Fresh capacity. Tasks still holding the old semaphore release
            # into the old object (see get_browser), so this cannot over-fill.
            leaked_slots = self._max_browsers - self._semaphore._value  # type: ignore[attr-defined]
            self._semaphore = asyncio.Semaphore(self._max_browsers)

            killed_pid = self._kill_driver(old_playwright)
            # Let the transport notice the dead process and fail pending calls.
            await asyncio.sleep(0)
            await self._stop_playwright(old_playwright, timeout=5.0)
            orphans = await asyncio.to_thread(self._reap_orphan_chromium)
        finally:
            if locked:
                self._lock.release()

        summary = {
            "generation": self._generation,
            "driver_pid_killed": killed_pid,
            "orphan_chromium_killed": orphans,
            "leaked_slots_recovered": leaked_slots,
        }
        logger.warning("Browser pool hard reset complete: %s", summary)
        return summary

    async def shutdown(self):
        """Shutdown the browser pool and cleanup resources."""
        self._shutting_down = True

        locked = False
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=self.LOCK_TIMEOUT)
            locked = True
        except asyncio.TimeoutError:
            logger.warning("Browser pool lock busy during shutdown; forcing driver stop")
        try:
            if self._playwright:
                logger.info("Shutting down Playwright instance...")
                playwright = self._playwright
                self._playwright = None
                self._initialized = False
                await self._stop_playwright(playwright, self.close_timeout)
                logger.info("Playwright instance stopped")
        finally:
            if locked:
                self._lock.release()

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    async def health_check(self) -> dict:
        proc = self._driver_process(self._playwright)
        driver_pid = getattr(proc, "pid", None) if proc is not None else None
        driver_alive = proc is not None and getattr(proc, "returncode", None) is None
        return {
            "initialized": self._initialized,
            "shutting_down": self._shutting_down,
            "generation": self._generation,
            "error_count": self._error_count,
            "consecutive_restart_failures": self._consecutive_restart_failures,
            "max_browsers": self._max_browsers,
            "available_slots": self._semaphore._value,  # type: ignore[attr-defined]
            "close_timeout_seconds": self.close_timeout,
            "hung_closes": self._hung_closes,
            "hard_resets": self._hard_resets,
            "last_hard_reset_at": self._last_hard_reset_at,
            "last_hard_reset_reason": self._last_hard_reset_reason,
            "driver_pid": driver_pid,
            "driver_alive": driver_alive,
        }


# Singleton instance — max_browsers configurable via BROWSER_POOL_MAX
browser_pool = BrowserPool()
