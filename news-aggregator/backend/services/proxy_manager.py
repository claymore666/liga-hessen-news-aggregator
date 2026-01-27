"""Proxy manager service for rotating free HTTP proxies.

Finds and maintains a pool of fast proxies on startup.
Only searches when pool drops below minimum threshold.
Persists known good proxies across restarts with 3-strike removal.
"""

import asyncio
import json
import logging
import random
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Persistent storage for known good proxies
KNOWN_PROXIES_FILE = Path(__file__).parent.parent / "data" / "known_proxies.json"


class ProxyManager:
    """Independent proxy management service.

    Finds MIN_WORKING_PROXIES fast proxies on startup, then stops.
    Periodically revalidates existing proxies and refills if needed.
    """

    # Multiple proxy sources for better coverage
    PROXY_SOURCES = [
        # Original sources
        "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
        "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
        "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
        # Additional sources - validated more frequently
        "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
        "https://raw.githubusercontent.com/officialputuid/KangProxy/KangProxy/xResults/RAW.txt",
        "https://vakhov.github.io/fresh-proxy-list/http.txt",
        "https://vakhov.github.io/fresh-proxy-list/https.txt",
        "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt",
        "https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt",
    ]

    # Validation settings
    VALIDATION_TIMEOUT = 3.0  # seconds - allow slightly slower proxies
    # Use HTTP URLs that don't block proxy requests
    VALIDATION_URLS = [
        "http://httpbin.org/ip",
        "http://ifconfig.me/ip",
        "http://icanhazip.com",
        "http://ident.me",
    ]
    MAX_LATENCY_MS = 2500  # Accept proxies under 2.5 seconds for HTTPS
    BATCH_SIZE = 100  # Test this many proxies per batch
    REVALIDATION_INTERVAL = 300  # Seconds between health checks (5 min)
    MAX_FAILURES = 3  # Remove proxy after this many consecutive failures
    KNOWN_PROXIES_TO_TRY_FIRST = 10  # Try this many from known list first

    def __init__(self):
        # Configurable pool sizes from settings
        self.min_working_proxies = settings.proxy_pool_min
        self.max_working_proxies = settings.proxy_pool_max
        self.max_known_proxies = settings.proxy_known_max
        self.working_proxies: list[dict] = []
        self.current_index: int = 0
        self.last_refresh: datetime | None = None
        self._lock = asyncio.Lock()
        self._all_proxies: list[str] = []
        self._tested_proxies: set[str] = set()
        self._background_task: asyncio.Task | None = None
        self._running = False
        self._initial_fill_complete = False
        # Known good proxies with failure tracking: {proxy: {latency, failures, last_success}}
        self._known_proxies: dict[str, dict] = {}
        # Per-connector proxy reservations: {connector_type: {proxy1, proxy2, ...}}
        self._reserved: dict[str, set[str]] = defaultdict(set)
        self._load_known_proxies()

    def _load_known_proxies(self) -> None:
        """Load known good proxies from persistent storage."""
        try:
            if KNOWN_PROXIES_FILE.exists():
                with open(KNOWN_PROXIES_FILE, 'r') as f:
                    data = json.load(f)
                    self._known_proxies = data.get("proxies", {})
                    logger.info(f"Loaded {len(self._known_proxies)} known proxies from storage")
        except Exception as e:
            logger.warning(f"Failed to load known proxies: {e}")
            self._known_proxies = {}

    def _save_known_proxies(self) -> None:
        """Save known good proxies to persistent storage."""
        try:
            KNOWN_PROXIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(KNOWN_PROXIES_FILE, 'w') as f:
                json.dump({
                    "proxies": self._known_proxies,
                    "last_updated": datetime.utcnow().isoformat(),
                }, f, indent=2)
            logger.debug(f"Saved {len(self._known_proxies)} known proxies to storage")
        except Exception as e:
            logger.warning(f"Failed to save known proxies: {e}")

    def _add_known_proxy(self, proxy: str, latency: float) -> None:
        """Add or update a known good proxy."""
        self._known_proxies[proxy] = {
            "latency": round(latency, 2),
            "failures": 0,
            "last_success": datetime.utcnow().isoformat(),
        }
        # Trim to max size, keeping lowest latency proxies
        if len(self._known_proxies) > self.max_known_proxies:
            sorted_proxies = sorted(
                self._known_proxies.items(),
                key=lambda x: x[1]["latency"]
            )
            self._known_proxies = dict(sorted_proxies[:self.max_known_proxies])
        self._save_known_proxies()

    def _record_proxy_failure(self, proxy: str) -> None:
        """Record a failure for a known proxy. Remove after MAX_FAILURES."""
        if proxy in self._known_proxies:
            self._known_proxies[proxy]["failures"] += 1
            if self._known_proxies[proxy]["failures"] >= self.MAX_FAILURES:
                del self._known_proxies[proxy]
                logger.info(f"Removed proxy {proxy} after {self.MAX_FAILURES} failures")
            self._save_known_proxies()

    def _record_proxy_success(self, proxy: str, latency: float) -> None:
        """Record a successful use of a proxy, resetting failure count."""
        if proxy in self._known_proxies:
            self._known_proxies[proxy]["failures"] = 0
            self._known_proxies[proxy]["latency"] = round(latency, 2)
            self._known_proxies[proxy]["last_success"] = datetime.utcnow().isoformat()
        else:
            self._add_known_proxy(proxy, latency)

    async def _try_known_proxies_first(self) -> int:
        """Try known good proxies first before searching for new ones.

        Returns number of working proxies found from known list.
        """
        if not self._known_proxies:
            logger.info("No known proxies to try")
            return 0

        # Sort by lowest latency and take first N
        sorted_known = sorted(
            self._known_proxies.items(),
            key=lambda x: x[1]["latency"]
        )[:self.KNOWN_PROXIES_TO_TRY_FIRST]

        if not sorted_known:
            return 0

        logger.info(f"Trying {len(sorted_known)} known proxies first...")
        found = 0

        for proxy, info in sorted_known:
            success, latency = await self.validate_proxy(proxy)
            if success:
                self.working_proxies.append({
                    "proxy": proxy,
                    "latency": round(latency, 2),
                    "last_checked": datetime.utcnow().isoformat(),
                })
                self._record_proxy_success(proxy, latency)
                found += 1
                logger.info(f"✓ Known proxy still works: {proxy} ({latency:.0f}ms)")
            else:
                self._record_proxy_failure(proxy)
                logger.info(f"✗ Known proxy failed: {proxy}")

        self.working_proxies.sort(key=lambda x: x["latency"])
        logger.info(f"Found {found}/{len(sorted_known)} working proxies from known list")
        return found

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

        except Exception as e:
            logger.debug(f"Proxy validation failed for {proxy}: {e}")
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
                # Also add to known proxies for persistence
                self._record_proxy_success(proxy, latency)
                new_fast += 1
                logger.info(f"✓ Found fast proxy: {proxy} ({latency:.0f}ms)")

        self.working_proxies.sort(key=lambda x: x["latency"])
        self.working_proxies = self.working_proxies[:self.max_working_proxies]
        self.last_refresh = datetime.utcnow()

        return new_fast

    async def _fill_pool(self):
        """Fill proxy pool until we have MIN_WORKING_PROXIES."""
        while len(self.working_proxies) < self.min_working_proxies:
            found = await self._search_batch()
            if found == 0:
                # No luck this batch, small delay before next
                await asyncio.sleep(1)

            logger.info(f"Proxy pool: {len(self.working_proxies)}/{self.min_working_proxies}")

    async def _revalidate_existing(self) -> int:
        """Revalidate existing proxies and remove dead ones. Returns removed count."""
        if not self.working_proxies:
            return 0

        logger.info(f"Health check: validating {len(self.working_proxies)} proxies...")

        still_working = []
        removed = 0

        for proxy_info in self.working_proxies:
            proxy = proxy_info["proxy"]
            success, latency = await self.validate_proxy(proxy)
            if success:
                proxy_info["latency"] = round(latency, 2)
                proxy_info["last_checked"] = datetime.utcnow().isoformat()
                proxy_info["failures"] = 0  # Reset failure counter on success
                still_working.append(proxy_info)
                # Update known proxies with success
                self._record_proxy_success(proxy, latency)
            else:
                # Track consecutive failures - only remove after MAX_FAILURES
                failures = proxy_info.get("failures", 0) + 1
                proxy_info["failures"] = failures
                if failures >= self.MAX_FAILURES:
                    logger.info(f"✗ Removing dead proxy after {failures} failures: {proxy}")
                    self._record_proxy_failure(proxy)
                    removed += 1
                else:
                    logger.debug(f"Proxy {proxy} failed ({failures}/{self.MAX_FAILURES}), keeping")
                    still_working.append(proxy_info)

        self.working_proxies = still_working
        self.working_proxies.sort(key=lambda x: x["latency"])

        logger.info(f"Health check complete: {len(self.working_proxies)} healthy, {removed} removed")
        return removed

    async def _background_maintenance(self):
        """Background task: fill pool on startup, then periodic health checks."""
        logger.info("🚀 Starting proxy manager...")
        self._running = True

        try:
            # Phase 0: Try known good proxies first
            if self._known_proxies:
                logger.info(f"Phase 0: Trying {len(self._known_proxies)} known proxies first...")
                await self._try_known_proxies_first()
                logger.info(f"Found {len(self.working_proxies)} working proxies from known list")

            # Phase 1: Fill remaining slots from fresh proxy lists
            if len(self.working_proxies) < self.min_working_proxies:
                logger.info(f"Phase 1: Finding {self.min_working_proxies - len(self.working_proxies)} more fast proxies (<{self.MAX_LATENCY_MS}ms)...")
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
                if len(self.working_proxies) < self.min_working_proxies:
                    logger.info(f"Pool below minimum ({len(self.working_proxies)}/{self.min_working_proxies}), refilling...")
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

    async def stop_background_search(self):
        """Stop the background proxy manager and wait for cleanup."""
        self._running = False
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
        logger.info("Proxy manager stopped")

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

    async def checkout_proxy(self, connector_type: str) -> str | None:
        """Reserve a proxy for exclusive use by a connector type.

        This ensures concurrent scrapers of the same type get different proxies,
        preventing multiple scrapers from using the same IP to hit rate-limited sites.

        Args:
            connector_type: The connector type (e.g., "x_scraper", "instagram_scraper")

        Returns:
            Reserved proxy string (ip:port) or None if no proxies available
        """
        async with self._lock:
            # Get set of all working proxy addresses
            working_set = {p["proxy"] for p in self.working_proxies}

            # Get proxies already reserved by this connector type
            reserved_by_type = self._reserved[connector_type]

            # Find available proxies (working but not reserved by this connector type)
            available = working_set - reserved_by_type

            if not available:
                logger.debug(f"No available proxies for {connector_type} "
                           f"(working={len(working_set)}, reserved={len(reserved_by_type)})")
                return None

            # Pick a random available proxy
            proxy = random.choice(list(available))
            self._reserved[connector_type].add(proxy)

            logger.debug(f"Checked out proxy {proxy} for {connector_type} "
                        f"({len(available)-1} remaining)")
            return proxy

    async def checkin_proxy(self, connector_type: str, proxy: str) -> None:
        """Release a reserved proxy back to the pool.

        Args:
            connector_type: The connector type that reserved the proxy
            proxy: The proxy to release
        """
        async with self._lock:
            self._reserved[connector_type].discard(proxy)
            logger.debug(f"Checked in proxy {proxy} for {connector_type}")

    def available_count(self, connector_type: str) -> int:
        """Get count of proxies available for a connector type.

        Args:
            connector_type: The connector type to check

        Returns:
            Number of working proxies not reserved by this connector type
        """
        working_set = {p["proxy"] for p in self.working_proxies}
        reserved_by_type = self._reserved.get(connector_type, set())
        return len(working_set - reserved_by_type)

    def get_status(self) -> dict:
        """Get current proxy pool status."""
        return {
            "working_count": len(self.working_proxies),
            "min_required": self.min_working_proxies,
            "max_working": self.max_working_proxies,
            "max_latency_ms": self.MAX_LATENCY_MS,
            "proxies": self.working_proxies,
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "current_index": self.current_index,
            "background_running": self._running,
            "initial_fill_complete": self._initial_fill_complete,
            "tested_count": len(self._tested_proxies),
            "total_available": len(self._all_proxies),
            "known_proxies_count": len(self._known_proxies),
            "known_proxies_max": self.max_known_proxies,
            "max_failures_before_removal": self.MAX_FAILURES,
            "reserved_by_type": {k: len(v) for k, v in self._reserved.items()},
        }


# Singleton instance
proxy_manager = ProxyManager()
