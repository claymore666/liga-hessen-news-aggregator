"""Proxy manager service for rotating free HTTP proxies."""

import asyncio
import logging
import time
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


class ProxyManager:
    """Independent proxy management service.

    Fetches free proxy lists, validates them, and provides
    round-robin access to working proxies.
    """

    PROXY_SOURCES = [
        "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    ]

    # Validation settings
    VALIDATION_TIMEOUT = 5.0  # seconds
    VALIDATION_URL = "http://httpbin.org/ip"
    MAX_PROXIES_TO_TEST = 50
    MAX_WORKING_PROXIES = 10

    def __init__(self):
        self.working_proxies: list[dict] = []
        self.current_index: int = 0
        self.last_refresh: datetime | None = None
        self._lock = asyncio.Lock()

    async def fetch_proxy_list(self) -> list[str]:
        """Fetch proxies from all sources."""
        all_proxies: set[str] = set()

        async with httpx.AsyncClient(timeout=10.0) as client:
            for source_url in self.PROXY_SOURCES:
                try:
                    response = await client.get(source_url)
                    response.raise_for_status()

                    # Parse proxy list (one per line, format: ip:port)
                    for line in response.text.strip().split("\n"):
                        line = line.strip()
                        if line and ":" in line:
                            # Handle formats like "ip:port" or "ip:port:extra"
                            parts = line.split(":")
                            if len(parts) >= 2:
                                proxy = f"{parts[0]}:{parts[1]}"
                                all_proxies.add(proxy)

                    logger.info(f"Fetched {len(all_proxies)} proxies from {source_url}")
                except Exception as e:
                    logger.warning(f"Failed to fetch from {source_url}: {e}")

        return list(all_proxies)

    async def validate_proxy(self, proxy: str) -> tuple[bool, float]:
        """Test proxy against httpbin.org/ip.

        Args:
            proxy: Proxy address in format "ip:port"

        Returns:
            Tuple of (success, latency_ms)
        """
        proxy_url = f"http://{proxy}"
        start_time = time.time()

        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=self.VALIDATION_TIMEOUT,
            ) as client:
                response = await client.get(self.VALIDATION_URL)
                response.raise_for_status()

                latency = (time.time() - start_time) * 1000  # ms
                return True, latency

        except Exception:
            return False, 0.0

    async def refresh_proxy_list(self) -> int:
        """Fetch and validate proxies.

        Called by scheduler periodically.

        Returns:
            Number of working proxies found.
        """
        async with self._lock:
            logger.info("Refreshing proxy list...")

            # 1. Fetch all proxies
            all_proxies = await self.fetch_proxy_list()
            if not all_proxies:
                logger.warning("No proxies fetched from sources")
                return len(self.working_proxies)

            logger.info(f"Fetched {len(all_proxies)} total proxies")

            # 2. Shuffle and take sample to test
            import random
            random.shuffle(all_proxies)
            proxies_to_test = all_proxies[: self.MAX_PROXIES_TO_TEST]

            # 3. Validate in parallel
            logger.info(f"Validating {len(proxies_to_test)} proxies...")
            tasks = [self.validate_proxy(proxy) for proxy in proxies_to_test]
            results = await asyncio.gather(*tasks)

            # 4. Collect working proxies with latency
            working = []
            for proxy, (success, latency) in zip(proxies_to_test, results):
                if success:
                    working.append({
                        "proxy": proxy,
                        "latency": round(latency, 2),
                        "last_checked": datetime.utcnow().isoformat(),
                    })

            # 5. Sort by latency and keep top N
            working.sort(key=lambda x: x["latency"])
            self.working_proxies = working[: self.MAX_WORKING_PROXIES]
            self.last_refresh = datetime.utcnow()
            self.current_index = 0

            logger.info(f"Found {len(self.working_proxies)} working proxies")
            return len(self.working_proxies)

    def get_next_proxy(self) -> str | None:
        """Get next proxy from round-robin rotation.

        Returns:
            Proxy address in format "ip:port" or None if no proxies available.
        """
        if not self.working_proxies:
            return None

        proxy_info = self.working_proxies[self.current_index % len(self.working_proxies)]
        self.current_index += 1
        return proxy_info["proxy"]

    def get_status(self) -> dict:
        """Get current proxy pool status.

        Returns:
            Status dictionary with proxy count and details.
        """
        return {
            "working_count": len(self.working_proxies),
            "proxies": self.working_proxies,
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "current_index": self.current_index,
        }


# Singleton instance
proxy_manager = ProxyManager()
