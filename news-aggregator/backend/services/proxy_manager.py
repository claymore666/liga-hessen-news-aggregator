"""Proxy manager service for rotating free HTTP proxies.

Finds and maintains two separate pools of proxies:
- HTTP pool: General purpose proxies for HTTP requests
- HTTPS pool: Proxies that support HTTPS CONNECT tunneling (for X.com, etc.)

Each pool has independent thresholds and is managed separately.
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
    """Independent proxy management service with separate HTTP and HTTPS pools.

    Maintains two pools:
    - http_proxies: General purpose, filled to min_http_proxies
    - https_proxies: HTTPS tunnel capable, filled to min_https_proxies
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
    VALIDATION_URLS = [
        "http://httpbin.org/ip",
        "http://ifconfig.me/ip",
        "http://icanhazip.com",
        "http://ident.me",
    ]
    MAX_LATENCY_MS = 2500  # Accept proxies under 2.5 seconds
    BATCH_SIZE = 100  # Test this many proxies per batch
    REVALIDATION_INTERVAL = 300  # Seconds between health checks (5 min)
    MAX_FAILURES = 3  # Remove proxy after this many consecutive failures
    KNOWN_PROXIES_TO_TRY_FIRST = 20  # Try this many from known list first

    def __init__(self):
        # Configurable pool sizes from settings
        self.min_http_proxies = settings.proxy_pool_min
        self.max_http_proxies = settings.proxy_pool_max
        self.min_https_proxies = settings.proxy_https_pool_min
        self.max_https_proxies = settings.proxy_https_pool_min + 5  # Small buffer
        self.max_known_proxies = settings.proxy_known_max

        # Separate pools
        self.http_proxies: list[dict] = []
        self.https_proxies: list[dict] = []

        # Round-robin indices
        self.http_index: int = 0
        self.https_index: int = 0

        self.last_refresh: datetime | None = None
        self._lock = asyncio.Lock()
        self._all_proxies: list[str] = []
        self._tested_proxies: set[str] = set()
        self._background_task: asyncio.Task | None = None
        self._running = False
        self._initial_fill_complete = False

        # Known good proxies: {proxy: {latency, failures, last_success, https_capable}}
        self._known_proxies: dict[str, dict] = {}

        # Per-connector proxy reservations: {connector_type: {proxy1, proxy2, ...}}
        self._reserved_http: dict[str, set[str]] = defaultdict(set)
        self._reserved_https: dict[str, set[str]] = defaultdict(set)

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

    def _add_known_proxy(self, proxy: str, latency: float, https_capable: bool = False) -> None:
        """Add or update a known good proxy."""
        self._known_proxies[proxy] = {
            "latency": round(latency, 2),
            "failures": 0,
            "last_success": datetime.utcnow().isoformat(),
            "https_capable": https_capable,
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

    def _record_proxy_success(self, proxy: str, latency: float, https_capable: bool) -> None:
        """Record a successful use of a proxy, resetting failure count."""
        if proxy in self._known_proxies:
            self._known_proxies[proxy]["failures"] = 0
            self._known_proxies[proxy]["latency"] = round(latency, 2)
            self._known_proxies[proxy]["last_success"] = datetime.utcnow().isoformat()
            self._known_proxies[proxy]["https_capable"] = https_capable
        else:
            self._add_known_proxy(proxy, latency, https_capable)

    def _add_to_pool(self, proxy: str, latency: float, https_capable: bool) -> None:
        """Add proxy to the appropriate pool."""
        proxy_info = {
            "proxy": proxy,
            "latency": round(latency, 2),
            "last_checked": datetime.utcnow().isoformat(),
            "failures": 0,
        }

        if https_capable:
            # Check if already in HTTPS pool
            existing = {p["proxy"] for p in self.https_proxies}
            if proxy not in existing and len(self.https_proxies) < self.max_https_proxies:
                self.https_proxies.append(proxy_info)
                self.https_proxies.sort(key=lambda x: x["latency"])
                logger.info(f"✓ Found HTTPS proxy: {proxy} ({latency:.0f}ms)")
        else:
            # Check if already in HTTP pool
            existing = {p["proxy"] for p in self.http_proxies}
            if proxy not in existing and len(self.http_proxies) < self.max_http_proxies:
                self.http_proxies.append(proxy_info)
                self.http_proxies.sort(key=lambda x: x["latency"])
                logger.info(f"✓ Found HTTP proxy: {proxy} ({latency:.0f}ms)")

        self._record_proxy_success(proxy, latency, https_capable)

    async def _try_known_proxies_first(self) -> tuple[int, int]:
        """Try known good proxies first. Returns (http_found, https_found)."""
        if not self._known_proxies:
            logger.info("No known proxies to try")
            return 0, 0

        # Sort by lowest latency and take first N
        sorted_known = sorted(
            self._known_proxies.items(),
            key=lambda x: x[1]["latency"]
        )[:self.KNOWN_PROXIES_TO_TRY_FIRST]

        if not sorted_known:
            return 0, 0

        logger.info(f"Trying {len(sorted_known)} known proxies first...")
        http_found = 0
        https_found = 0

        for proxy, info in sorted_known:
            success, latency = await self.validate_proxy(proxy)
            if success:
                # Use stored https_capable or re-test if unknown
                https_capable = info.get("https_capable", False)
                if not https_capable:
                    https_capable = await self.validate_https_tunnel(proxy)

                self._add_to_pool(proxy, latency, https_capable)
                if https_capable:
                    https_found += 1
                else:
                    http_found += 1
            else:
                self._record_proxy_failure(proxy)
                logger.debug(f"✗ Known proxy failed: {proxy}")

        logger.info(f"Found {http_found} HTTP, {https_found} HTTPS from known list")
        return http_found, https_found

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
        """Test proxy with strict latency requirement (<2500ms)."""
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

    async def validate_https_tunnel(self, proxy: str) -> bool:
        """Test if proxy supports HTTPS CONNECT tunnel to x.com."""
        import socket

        try:
            proxy_host, proxy_port = proxy.split(":")
            proxy_port = int(proxy_port)
        except ValueError:
            return False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((proxy_host, proxy_port))

            connect_request = f"CONNECT x.com:443 HTTP/1.1\r\nHost: x.com:443\r\n\r\n"
            sock.send(connect_request.encode())

            response = sock.recv(1024).decode()
            sock.close()

            if "200" in response.split("\r\n")[0]:
                logger.debug(f"HTTPS tunnel OK for {proxy}")
                return True
            else:
                logger.debug(f"HTTPS tunnel failed for {proxy}: {response.split(chr(13))[0]}")
                return False

        except Exception as e:
            logger.debug(f"HTTPS tunnel test failed for {proxy}: {e}")
            return False

    async def _search_batch(self) -> tuple[int, int]:
        """Test a batch of proxies. Returns (http_found, https_found)."""
        untested = [p for p in self._all_proxies if p not in self._tested_proxies]

        if not untested:
            logger.info("All proxies tested, fetching fresh list...")
            self._all_proxies = await self.fetch_proxy_list()
            self._tested_proxies.clear()
            untested = self._all_proxies

        if not untested:
            return 0, 0

        random.shuffle(untested)
        batch = untested[:self.BATCH_SIZE]
        self._tested_proxies.update(batch)

        logger.info(f"Testing batch of {len(batch)} proxies...")
        tasks = [self.validate_proxy(proxy) for proxy in batch]
        results = await asyncio.gather(*tasks)

        # Collect working proxies
        working_batch = []
        existing_http = {p["proxy"] for p in self.http_proxies}
        existing_https = {p["proxy"] for p in self.https_proxies}
        existing = existing_http | existing_https

        for proxy, (success, latency) in zip(batch, results):
            if success and proxy not in existing:
                working_batch.append((proxy, latency))

        if not working_batch:
            return 0, 0

        # Test HTTPS capability in parallel
        https_tasks = [self.validate_https_tunnel(p) for p, _ in working_batch]
        https_results = await asyncio.gather(*https_tasks)

        http_found = 0
        https_found = 0

        for (proxy, latency), https_capable in zip(working_batch, https_results):
            # Only add if we need more in that pool
            if https_capable and len(self.https_proxies) < self.max_https_proxies:
                self._add_to_pool(proxy, latency, https_capable=True)
                https_found += 1
            elif not https_capable and len(self.http_proxies) < self.max_http_proxies:
                self._add_to_pool(proxy, latency, https_capable=False)
                http_found += 1

        self.last_refresh = datetime.utcnow()
        return http_found, https_found

    def _pools_filled(self) -> bool:
        """Check if both pools meet their minimums."""
        return (len(self.http_proxies) >= self.min_http_proxies and
                len(self.https_proxies) >= self.min_https_proxies)

    async def _fill_pools(self):
        """Fill both pools until they meet their minimums."""
        max_batches = 30  # Limit search to avoid infinite loops
        batches_tried = 0

        while not self._pools_filled() and batches_tried < max_batches:
            http_found, https_found = await self._search_batch()
            batches_tried += 1

            if http_found == 0 and https_found == 0:
                await asyncio.sleep(1)

            logger.info(f"Pools: HTTP {len(self.http_proxies)}/{self.min_http_proxies}, "
                       f"HTTPS {len(self.https_proxies)}/{self.min_https_proxies}")

        if len(self.http_proxies) < self.min_http_proxies:
            logger.warning(f"Could not fill HTTP pool: {len(self.http_proxies)}/{self.min_http_proxies}")
        if len(self.https_proxies) < self.min_https_proxies:
            logger.warning(f"Could not fill HTTPS pool: {len(self.https_proxies)}/{self.min_https_proxies}")

    async def _revalidate_pool(self, pool: list[dict], pool_name: str) -> int:
        """Revalidate a pool and remove dead proxies. Returns removed count."""
        if not pool:
            return 0

        still_working = []
        removed = 0

        for proxy_info in pool:
            proxy = proxy_info["proxy"]
            success, latency = await self.validate_proxy(proxy)
            if success:
                proxy_info["latency"] = round(latency, 2)
                proxy_info["last_checked"] = datetime.utcnow().isoformat()
                proxy_info["failures"] = 0
                still_working.append(proxy_info)
            else:
                failures = proxy_info.get("failures", 0) + 1
                proxy_info["failures"] = failures
                if failures >= self.MAX_FAILURES:
                    logger.info(f"✗ Removing dead {pool_name} proxy: {proxy}")
                    self._record_proxy_failure(proxy)
                    removed += 1
                else:
                    still_working.append(proxy_info)

        pool.clear()
        pool.extend(still_working)
        pool.sort(key=lambda x: x["latency"])

        return removed

    async def _revalidate_existing(self) -> tuple[int, int]:
        """Revalidate both pools. Returns (http_removed, https_removed)."""
        logger.info(f"Health check: HTTP={len(self.http_proxies)}, HTTPS={len(self.https_proxies)}")

        http_removed = await self._revalidate_pool(self.http_proxies, "HTTP")
        https_removed = await self._revalidate_pool(self.https_proxies, "HTTPS")

        logger.info(f"Health check complete: HTTP={len(self.http_proxies)} (-{http_removed}), "
                   f"HTTPS={len(self.https_proxies)} (-{https_removed})")
        return http_removed, https_removed

    async def _background_maintenance(self):
        """Background task: fill pools on startup, then periodic health checks."""
        logger.info("🚀 Starting proxy manager (split pools)...")
        self._running = True

        try:
            # Phase 0: Try known good proxies first
            if self._known_proxies:
                logger.info(f"Phase 0: Trying {len(self._known_proxies)} known proxies...")
                await self._try_known_proxies_first()

            # Phase 1: Fill remaining slots
            if not self._pools_filled():
                logger.info("Phase 1: Finding more proxies...")
                self._all_proxies = await self.fetch_proxy_list()
                await self._fill_pools()

            self._initial_fill_complete = True
            logger.info(f"✅ Initial fill complete: HTTP={len(self.http_proxies)}, "
                       f"HTTPS={len(self.https_proxies)}")

            # Phase 2: Maintenance mode
            while self._running:
                await asyncio.sleep(self.REVALIDATION_INTERVAL)
                await self._revalidate_existing()

                if not self._pools_filled():
                    logger.info("Pool below minimum, refilling...")
                    await self._fill_pools()

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
        """Stop the background proxy manager."""
        self._running = False
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
        logger.info("Proxy manager stopped")

    async def refresh_proxy_list(self) -> int:
        """Manual refresh - clear pools and refill."""
        async with self._lock:
            self._all_proxies = await self.fetch_proxy_list()
            self._tested_proxies.clear()
            self.http_proxies.clear()
            self.https_proxies.clear()

            await self._fill_pools()
            return len(self.http_proxies) + len(self.https_proxies)

    def get_next_proxy(self) -> str | None:
        """Get next HTTP proxy from round-robin rotation."""
        if not self.http_proxies:
            return None

        proxy_info = self.http_proxies[self.http_index % len(self.http_proxies)]
        self.http_index += 1
        return proxy_info["proxy"]

    async def checkout_proxy(self, connector_type: str, prefer_https: bool = False) -> str | None:
        """Reserve a proxy for exclusive use by a connector type.

        Args:
            connector_type: The connector type (e.g., "x_scraper", "instagram_scraper")
            prefer_https: If True, draw from HTTPS pool. If False, draw from HTTP pool.

        Returns:
            Reserved proxy string (ip:port) or None if no proxies available
        """
        async with self._lock:
            if prefer_https:
                pool = self.https_proxies
                reserved = self._reserved_https[connector_type]
                pool_name = "HTTPS"
            else:
                pool = self.http_proxies
                reserved = self._reserved_http[connector_type]
                pool_name = "HTTP"

            # Find available proxies
            available = [p["proxy"] for p in pool if p["proxy"] not in reserved]

            if not available:
                logger.debug(f"No available {pool_name} proxies for {connector_type} "
                           f"(pool={len(pool)}, reserved={len(reserved)})")
                return None

            proxy = random.choice(available)
            reserved.add(proxy)

            logger.debug(f"Checked out {pool_name} proxy {proxy} for {connector_type} "
                        f"({len(available)-1} remaining)")
            return proxy

    async def checkin_proxy(self, connector_type: str, proxy: str, is_https: bool = False) -> None:
        """Release a reserved proxy back to the pool."""
        async with self._lock:
            if is_https:
                self._reserved_https[connector_type].discard(proxy)
            else:
                self._reserved_http[connector_type].discard(proxy)
            logger.debug(f"Checked in proxy {proxy} for {connector_type}")

    def available_count(self, connector_type: str, https: bool = False) -> int:
        """Get count of proxies available for a connector type."""
        if https:
            pool = self.https_proxies
            reserved = self._reserved_https.get(connector_type, set())
        else:
            pool = self.http_proxies
            reserved = self._reserved_http.get(connector_type, set())

        return len([p for p in pool if p["proxy"] not in reserved])

    # Legacy property for backward compatibility
    @property
    def working_proxies(self) -> list[dict]:
        """Combined list of all working proxies (for backward compatibility)."""
        return self.http_proxies + self.https_proxies

    def get_status(self) -> dict:
        """Get current proxy pool status."""
        return {
            "http_count": len(self.http_proxies),
            "https_count": len(self.https_proxies),
            "http_min_required": self.min_http_proxies,
            "https_min_required": self.min_https_proxies,
            "http_max": self.max_http_proxies,
            "https_max": self.max_https_proxies,
            "max_latency_ms": self.MAX_LATENCY_MS,
            "http_proxies": self.http_proxies,
            "https_proxies": self.https_proxies,
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "background_running": self._running,
            "initial_fill_complete": self._initial_fill_complete,
            "tested_count": len(self._tested_proxies),
            "total_available": len(self._all_proxies),
            "known_proxies_count": len(self._known_proxies),
            "reserved_http": {k: len(v) for k, v in self._reserved_http.items()},
            "reserved_https": {k: len(v) for k, v in self._reserved_https.items()},
            # Legacy fields for backward compatibility
            "working_count": len(self.http_proxies) + len(self.https_proxies),
            "min_required": self.min_http_proxies,
            "min_https_required": self.min_https_proxies,
        }


# Singleton instance
proxy_manager = ProxyManager()
