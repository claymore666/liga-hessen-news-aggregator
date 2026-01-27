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
from contextlib import asynccontextmanager
from typing import Optional

from playwright.async_api import async_playwright, Browser, Playwright

logger = logging.getLogger(__name__)


class BrowserPool:
    """
    Singleton browser pool using a shared Playwright instance.

    Instead of each scraper creating its own Playwright driver (node process),
    this pool maintains a single driver and creates/recycles browsers from it.
    """

    def __init__(self, max_browsers: int = 8, error_threshold: int = 10):
        """
        Initialize the browser pool.

        Args:
            max_browsers: Maximum number of concurrent browsers
            error_threshold: Number of consecutive errors before restarting driver
        """
        self._playwright: Optional[Playwright] = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_browsers)
        self._initialized = False
        self._shutting_down = False
        self._error_count = 0
        self._error_threshold = error_threshold
        self._success_count = 0

    async def _ensure_initialized(self) -> Playwright:
        """Ensure Playwright is initialized, creating it if needed."""
        if self._playwright is not None:
            return self._playwright

        async with self._lock:
            # Double-check after acquiring lock
            if self._playwright is not None:
                return self._playwright

            if self._shutting_down:
                raise RuntimeError("Browser pool is shutting down")

            logger.info("Initializing shared Playwright instance...")
            self._playwright = await async_playwright().start()
            self._initialized = True
            logger.info("Playwright instance ready")
            return self._playwright

    @asynccontextmanager
    async def get_browser(
        self,
        headless: bool = True,
        args: Optional[list[str]] = None,
    ):
        """
        Get a browser from the pool.

        This is a context manager that:
        1. Waits for a slot in the semaphore
        2. Launches a browser from the shared Playwright instance
        3. Yields the browser for use
        4. Closes the browser when done

        Args:
            headless: Whether to run in headless mode
            args: Additional browser launch arguments

        Yields:
            Browser instance
        """
        if self._shutting_down:
            raise RuntimeError("Browser pool is shutting down")

        browser: Optional[Browser] = None

        # Wait for a slot
        async with self._semaphore:
            try:
                playwright = await self._ensure_initialized()

                # Default args for stealth
                launch_args = args or [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ]

                # Launch browser with timeout
                browser = await asyncio.wait_for(
                    playwright.chromium.launch(
                        headless=headless,
                        args=launch_args,
                    ),
                    timeout=30.0,
                )

                yield browser

                # Success - reset error count
                self._error_count = 0
                self._success_count += 1

            except asyncio.TimeoutError:
                logger.error("Browser launch timeout")
                await self._handle_error()
                raise
            except Exception as e:
                logger.error(f"Browser error: {e}")
                await self._handle_error()
                raise
            finally:
                if browser:
                    try:
                        await browser.close()
                    except Exception as e:
                        logger.debug(f"Error closing browser: {e}")

    async def _handle_error(self):
        """Handle browser error - restart driver if too many consecutive errors."""
        self._error_count += 1
        if self._error_count >= self._error_threshold:
            logger.warning(
                f"Browser error threshold reached ({self._error_count} errors), "
                "restarting Playwright driver..."
            )
            await self._restart_driver()

    async def _restart_driver(self):
        """Restart the Playwright driver."""
        async with self._lock:
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.debug(f"Error stopping Playwright during restart: {e}")
                self._playwright = None
                self._initialized = False

            # Reinitialize
            try:
                self._playwright = await async_playwright().start()
                self._initialized = True
                self._error_count = 0
                logger.info("Playwright driver restarted successfully")
            except Exception as e:
                logger.error(f"Failed to restart Playwright driver: {e}")

    async def shutdown(self):
        """Shutdown the browser pool and cleanup resources."""
        self._shutting_down = True

        async with self._lock:
            if self._playwright:
                logger.info("Shutting down Playwright instance...")
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.warning(f"Error stopping Playwright: {e}")
                finally:
                    self._playwright = None
                    self._initialized = False
                logger.info("Playwright instance stopped")

    @property
    def is_initialized(self) -> bool:
        """Check if the pool is initialized."""
        return self._initialized

    async def health_check(self) -> dict:
        """
        Get health status of the browser pool.

        Returns:
            Dict with status information
        """
        return {
            "initialized": self._initialized,
            "shutting_down": self._shutting_down,
            "available_slots": self._semaphore._value,
            "max_browsers": self._semaphore._value + (
                self._semaphore._value - self._semaphore._value
            ),
        }


# Singleton instance
# Keep concurrency low to avoid overwhelming the shared Playwright driver
# When one browser crashes, it can cascade to affect others
browser_pool = BrowserPool(max_browsers=4)
