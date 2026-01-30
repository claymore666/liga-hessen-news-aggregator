"""
Shared browser pool for Playwright-based scrapers.

This module provides a singleton Playwright instance to avoid spawning
multiple node driver processes. Each driver process consumes significant
resources, and without pooling they can accumulate and cause
"Resource temporarily unavailable" (Errno 11) errors.

Usage:
    from services.browser_pool import browser_pool

    async with browser_pool.get_browser() as browser:
        context = await browser.new_context(...)
        page = await context.new_page()
        # ... use page ...
        await context.close()
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from playwright.async_api import async_playwright, Browser, Playwright

logger = logging.getLogger(__name__)


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

    def __init__(self, max_browsers: int = 8, error_threshold: int = 10):
        self._playwright: Optional[Playwright] = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_browsers)
        self._initialized = False
        self._shutting_down = False
        self._error_count = 0
        self._error_threshold = error_threshold
        self._success_count = 0
        self._generation = 0  # incremented on each restart
        self._last_restart_attempt = 0.0
        self._consecutive_restart_failures = 0

    async def _ensure_initialized(self) -> Playwright:
        """Ensure Playwright is initialized, creating it if needed."""
        if self._playwright is not None:
            return self._playwright

        async with self._lock:
            if self._playwright is not None:
                return self._playwright

            if self._shutting_down:
                raise RuntimeError("Browser pool is shutting down")

            logger.info("Initializing shared Playwright instance...")
            self._playwright = await async_playwright().start()
            self._initialized = True
            self._generation += 1
            self._error_count = 0
            self._consecutive_restart_failures = 0
            logger.info("Playwright instance ready (generation %d)", self._generation)
            return self._playwright

    @asynccontextmanager
    async def get_browser(
        self,
        headless: bool = True,
        args: Optional[list[str]] = None,
    ):
        if self._shutting_down:
            raise RuntimeError("Browser pool is shutting down")

        browser: Optional[Browser] = None

        async with self._semaphore:
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
            except Exception as e:
                logger.error("Browser error: %s", e)
                await self._handle_error(gen_before)
                raise
            finally:
                if browser:
                    try:
                        await browser.close()
                    except Exception as e:
                        logger.debug("Error closing browser: %s", e)

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

            # Stop existing driver
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.debug("Error stopping Playwright during restart: %s", e)
                self._playwright = None
                self._initialized = False

            # Reinitialize
            try:
                self._playwright = await async_playwright().start()
                self._initialized = True
                self._generation += 1
                self._error_count = 0
                self._consecutive_restart_failures = 0
                logger.info(
                    "Playwright driver restarted successfully (generation %d)",
                    self._generation,
                )
            except Exception as e:
                self._consecutive_restart_failures += 1
                logger.error(
                    "Failed to restart Playwright driver (attempt %d/%d): %s",
                    self._consecutive_restart_failures,
                    self.MAX_RESTART_FAILURES,
                    e,
                )

    async def shutdown(self):
        """Shutdown the browser pool and cleanup resources."""
        self._shutting_down = True

        async with self._lock:
            if self._playwright:
                logger.info("Shutting down Playwright instance...")
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.warning("Error stopping Playwright: %s", e)
                finally:
                    self._playwright = None
                    self._initialized = False
                logger.info("Playwright instance stopped")

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    async def health_check(self) -> dict:
        return {
            "initialized": self._initialized,
            "shutting_down": self._shutting_down,
            "generation": self._generation,
            "error_count": self._error_count,
            "consecutive_restart_failures": self._consecutive_restart_failures,
        }


# Singleton instance
browser_pool = BrowserPool(max_browsers=4)
