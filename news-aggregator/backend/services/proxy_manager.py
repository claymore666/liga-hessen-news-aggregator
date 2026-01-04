"""Proxy manager service for rotating free HTTP proxies.

Finds and maintains a pool of fast proxies on startup.
Only searches when pool drops below minimum threshold.
"""

import asyncio
import logging
import random
import time
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


class ProxyManager:
    """Independent proxy management service.

    Finds MIN_WORKING_PROXIES fast proxies on startup, then stops.
    Periodically revalidates existing proxies and refills if needed.
    """

    # Multiple proxy sources for better coverage
    PROXY_SOURCES = [
        "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
        "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
        "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
    ]

    # Validation settings
    VALIDATION_TIMEOUT = 2.0  # seconds - strict timeout for fast proxies
    VALIDATION_URLS = [
        "http://icanhazip.com",
        "http://api.ipify.org",
        "http://checkip.amazonaws.com",
        "http://ipinfo.io/ip",
    ]
    MAX_LATENCY_MS = 500  # Only accept proxies faster than this
    MIN_WORKING_PROXIES = 10  # Minimum pool size to maintain
    MAX_WORKING_PROXIES = 15  # Keep a few extra as buffer
    BATCH_SIZE = 100  # Test this many proxies per batch
    REVALIDATION_INTERVAL = 300  # Seconds between health checks (5 min)

    def __init__(self):
        self.working_proxies: list[dict] = []
        self.current_index: int = 0
        self.last_refresh: datetime | None = None
        self._lock = asyncio.Lock()
        self._all_proxies: list[str] = []
        self._tested_proxies: set[str] = set()
        self._background_task: asyncio.Task | None = None
        self._running = False
        self._initial_fill_complete = False

    async def fetch_proxy_list(self) -> list[str]:
        """Fetch proxies from all sources in parallel."""
        all_proxies: set[str] = set()

        async with httpx.AsyncClient(timeout=10.0) as client:
            tasks = [self._fetch_source(client, url) for url in self.PROXY_SOURCES]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, set):
                    all_proxies.update(result)

        logger.info(f"Fetched {len(all_proxies)} unique proxies from {len(self.PROXY_SOURCES)} sources")
        return list(all_proxies)

    async def _fetch_source(self, client: httpx.AsyncClient, source_url: str) -> set[str]:
        """Fetch proxies from a single source."""
        proxies: set[str] = set()
        try:
            response = await client.get(source_url)
            response.raise_for_status()

            for line in response.text.strip().split("\n"):
                line = line.strip()
                if line and ":" in line:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        proxy = f"{parts[0]}:{parts[1]}"
                        proxies.add(proxy)
        except Exception as e:
            logger.warning(f"Failed to fetch from {source_url.split('/')[-1]}: {e}")

        return proxies

    async def validate_proxy(self, proxy: str) -> tuple[bool, float]:
        """Test proxy with strict latency requirement (<500ms)."""
        proxy_url = f"http://{proxy}"
        validation_url = random.choice(self.VALIDATION_URLS)
        start_time = time.time()

        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=self.VALIDATION_TIMEOUT,
            ) as client:
                response = await client.get(validation_url)
                response.raise_for_status()

                latency = (time.time() - start_time) * 1000

                if latency <= self.MAX_LATENCY_MS:
                    return True, latency
                return False, latency

        except Exception:
            return False, 0.0

    async def _search_batch(self) -> int:
        """Test a batch of proxies and add fast ones."""
        untested = [p for p in self._all_proxies if p not in self._tested_proxies]

        if not untested:
            logger.info("All proxies tested, fetching fresh list...")
            self._all_proxies = await self.fetch_proxy_list()
            self._tested_proxies.clear()
            untested = self._all_proxies

        if not untested:
            return 0

        random.shuffle(untested)
        batch = untested[:self.BATCH_SIZE]
        self._tested_proxies.update(batch)

        logger.info(f"Testing batch of {len(batch)} proxies...")
        tasks = [self.validate_proxy(proxy) for proxy in batch]
        results = await asyncio.gather(*tasks)

        new_fast = 0
        existing = {p["proxy"] for p in self.working_proxies}

        for proxy, (success, latency) in zip(batch, results):
            if success and proxy not in existing:
                self.working_proxies.append({
                    "proxy": proxy,
                    "latency": round(latency, 2),
                    "last_checked": datetime.utcnow().isoformat(),
                })
                new_fast += 1
                logger.info(f"✓ Found fast proxy: {proxy} ({latency:.0f}ms)")

        self.working_proxies.sort(key=lambda x: x["latency"])
        self.working_proxies = self.working_proxies[:self.MAX_WORKING_PROXIES]
        self.last_refresh = datetime.utcnow()

        return new_fast

    async def _fill_pool(self):
        """Fill proxy pool until we have MIN_WORKING_PROXIES."""
        while len(self.working_proxies) < self.MIN_WORKING_PROXIES:
            found = await self._search_batch()
            if found == 0:
                # No luck this batch, small delay before next
                await asyncio.sleep(1)

            logger.info(f"Proxy pool: {len(self.working_proxies)}/{self.MIN_WORKING_PROXIES}")

    async def _revalidate_existing(self) -> int:
        """Revalidate existing proxies and remove dead ones. Returns removed count."""
        if not self.working_proxies:
            return 0

        logger.info(f"Health check: validating {len(self.working_proxies)} proxies...")

        still_working = []
        removed = 0

        for proxy_info in self.working_proxies:
            success, latency = await self.validate_proxy(proxy_info["proxy"])
            if success:
                proxy_info["latency"] = round(latency, 2)
                proxy_info["last_checked"] = datetime.utcnow().isoformat()
                still_working.append(proxy_info)
            else:
                logger.info(f"✗ Removing dead proxy: {proxy_info['proxy']}")
                removed += 1

        self.working_proxies = still_working
        self.working_proxies.sort(key=lambda x: x["latency"])

        logger.info(f"Health check complete: {len(self.working_proxies)} healthy, {removed} removed")
        return removed

    async def _background_maintenance(self):
        """Background task: fill pool on startup, then periodic health checks."""
        logger.info("🚀 Starting proxy manager...")
        self._running = True

        try:
            # Phase 1: Initial fill
            logger.info(f"Phase 1: Finding {self.MIN_WORKING_PROXIES} fast proxies (<{self.MAX_LATENCY_MS}ms)...")
            self._all_proxies = await self.fetch_proxy_list()
            await self._fill_pool()

            self._initial_fill_complete = True
            logger.info(f"✅ Initial fill complete: {len(self.working_proxies)} proxies ready")

            # Phase 2: Maintenance mode
            while self._running:
                await asyncio.sleep(self.REVALIDATION_INTERVAL)

                # Health check
                removed = await self._revalidate_existing()

                # Refill if below minimum
                if len(self.working_proxies) < self.MIN_WORKING_PROXIES:
                    logger.info(f"Pool below minimum ({len(self.working_proxies)}/{self.MIN_WORKING_PROXIES}), refilling...")
                    await self._fill_pool()

        except asyncio.CancelledError:
            logger.info("Proxy manager stopped")
        except Exception as e:
            logger.error(f"Proxy manager error: {e}")
        finally:
            self._running = False

    def start_background_search(self):
        """Start the background proxy manager."""
        if self._background_task is None or self._background_task.done():
            self._background_task = asyncio.create_task(self._background_maintenance())

    def stop_background_search(self):
        """Stop the background proxy manager."""
        self._running = False
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()

    async def refresh_proxy_list(self) -> int:
        """Manual refresh - clear pool and refill."""
        async with self._lock:
            self._all_proxies = await self.fetch_proxy_list()
            self._tested_proxies.clear()
            self.working_proxies.clear()

            await self._fill_pool()
            return len(self.working_proxies)

    def get_next_proxy(self) -> str | None:
        """Get next proxy from round-robin rotation."""
        if not self.working_proxies:
            return None

        proxy_info = self.working_proxies[self.current_index % len(self.working_proxies)]
        self.current_index += 1
        return proxy_info["proxy"]

    def get_status(self) -> dict:
        """Get current proxy pool status."""
        return {
            "working_count": len(self.working_proxies),
            "min_required": self.MIN_WORKING_PROXIES,
            "max_latency_ms": self.MAX_LATENCY_MS,
            "proxies": self.working_proxies,
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "current_index": self.current_index,
            "background_running": self._running,
            "initial_fill_complete": self._initial_fill_complete,
            "tested_count": len(self._tested_proxies),
            "total_available": len(self._all_proxies),
        }


# Singleton instance
proxy_manager = ProxyManager()
