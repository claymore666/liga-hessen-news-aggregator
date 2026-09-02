"""Proxy manager service for rotating free HTTP proxies.

Finds and maintains two separate pools of proxies:
- HTTP pool: General purpose proxies for HTTP requests
- HTTPS pool: Proxies that support HTTPS CONNECT tunneling (for X.com, etc.)

Each pool has independent thresholds and is managed separately.
"""

import asyncio
import ipaddress
import json
import logging
import random
import re
import socket
import ssl
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import httpx

from config import settings
from database import utcnow

logger = logging.getLogger(__name__)

# Persistent storage for known good proxies
KNOWN_PROXIES_FILE = Path(__file__).parent.parent / "data" / "known_proxies.json"

# Leading scheme on a proxy line, e.g. "http://1.2.3.4:8080" or "socks5://..."
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
# First IPv4 address appearing in a validation response body
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def parse_proxy_line(line: str) -> str | None:
    """Extract a canonical ``ip:port`` proxy from one line of a proxy list.

    Sources are wildly inconsistent: bare ``ip:port``, scheme-prefixed
    ``http://ip:port``, ``ip:port:country:anonymity``, comment lines and
    CSV-ish headers all appear. Anything that is not a real IPv4 address plus a
    valid port is rejected rather than passed through as a junk "proxy".
    """
    line = line.strip()
    if not line or line.startswith(("#", "//", ";")):
        return None

    # Take the first whitespace/comma separated field ("1.2.3.4:80  Germany")
    line = re.split(r"[\s,]+", line, maxsplit=1)[0]
    line = _SCHEME_RE.sub("", line)
    if not line:
        return None

    # "ip:port", optionally followed by extra ":"-separated metadata
    parts = line.split(":")
    if len(parts) < 2:
        return None

    host, port_str = parts[0], parts[1]
    try:
        ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        return None

    try:
        port = int(port_str)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None

    return f"{host}:{port}"


def _source_label(url: str) -> str:
    """Short but unambiguous identifier for a proxy source.

    Several sources are literally named ``http.txt``, so the bare filename is
    useless in logs.
    """
    return "/".join(url.split("/")[-3:]) or url


class ProxyManager:
    """Independent proxy management service with separate HTTP and HTTPS pools.

    Maintains two pools:
    - http_proxies: General purpose, filled to min_http_proxies
    - https_proxies: HTTPS tunnel capable, collected up to https_target
    """

    # Multiple proxy sources for better coverage.
    # Line formats differ per source (bare ip:port, scheme-prefixed, ip:port:country,
    # comment lines) — everything goes through parse_proxy_line.
    # Measured 2026-09-02: each list sampled at 40 entries and run through the
    # same two checks the pools use — a plain-HTTP fetch, and a CONNECT plus a
    # real TLS handshake to x.com. Hits per 40 sampled, as https/http:
    #
    #   monosans        687   14/4     vakhov http     524    0/16
    #   KangProxy      1523   11/7     proxifly        631    1/12
    #   elliottophellia 1045  10/12    sunny9577      1615    1/7
    #   proxyscrape v2  138    9/9     TheSpeedX      2925    1/4
    #   proxyscrape v4  551    5/7     ShiftyTR         40    1/0
    #   Zaeem20         409    4/1
    #
    # Dropped on the same measurement, and why size matters as much as rate:
    # MuRongPIG/Proxy-Master scored 0/3 yet held 20,000 entries — 73% of the
    # whole candidate pool — so almost every random batch was drawn from a list
    # that yields nothing, which is the actual reason the HTTPS pool sat at one
    # proxy. Also dropped for scoring 0 on both checks: jetkai (1801),
    # clarketm (400), vakhov/https (6), roosterkid/HTTPS_RAW (64, 0/2) and
    # zloi-user/hideip.me (1080, 0/1). Removing them cut the pool from 27,400 to
    # ~10,100 candidates and raised the expected hit rate from ~2% to ~12%
    # (HTTPS) and ~10% to ~17% (HTTP).
    #
    # Note the shape of the result: a list being *named* https predicts nothing.
    # The three best CONNECT sources are general http lists that happen to be
    # freshly checked. Re-run the measurement before adding or trusting a source.
    PROXY_SOURCES = [
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/officialputuid/KangProxy/main/http/http.txt",
        "https://raw.githubusercontent.com/elliottophellia/proxylist/master/results/http/global/http_checked.txt",
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&ssl=yes&anonymity=all",
        "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&proxy_format=ipport&format=text",
        "https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/master/https.txt",
        "https://vakhov.github.io/fresh-proxy-list/http.txt",
        "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
        "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt",
        "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    ]

    # Validation settings
    VALIDATION_TIMEOUT = 3.0  # seconds - allow slightly slower proxies
    # Endpoints that echo the caller's IP as plain text, so we can verify the
    # response really came back through the proxy.
    VALIDATION_URLS = [
        "http://httpbin.org/ip",
        "http://ifconfig.me/ip",
        "http://icanhazip.com",
        "http://ident.me",
    ]
    VALIDATION_ATTEMPTS = 2  # Distinct endpoints to try before rejecting a proxy
    MAX_RESPONSE_BYTES = 4096  # Hard cap on validation response size
    MAX_LATENCY_MS = 2500  # Accept proxies under 2.5 seconds
    MAX_PROXIES_PER_SOURCE = 20000  # Keep one huge list from swamping the others
    BATCH_SIZE = 25  # Test this many proxies per batch (keep low to avoid CPU spikes)
    BATCH_COOLDOWN = 5  # Seconds between batches to avoid hammering
    REVALIDATION_INTERVAL = 600  # Seconds between health checks (10 min)
    MAX_FAILURES = 3  # Remove proxy after this many consecutive failures
    KNOWN_PROXIES_TO_TRY_FIRST = 20  # Try this many from known list first
    TUNNEL_TIMEOUT = 5.0  # seconds for the CONNECT + TLS handshake check

    def __init__(self):
        # Configurable pool sizes from settings
        self.min_http_proxies = settings.proxy_pool_min
        self.max_http_proxies = settings.proxy_pool_max
        # Aspiration vs reality: we want proxy_https_pool_target, but HTTPS-capable
        # proxies are scarce and a handful is a normal outcome. Only a pool below
        # the floor counts as degraded.
        self.https_target = settings.proxy_https_pool_target
        self.https_floor = settings.proxy_https_pool_floor
        self.https_probe_budget = settings.proxy_https_probe_budget
        self.max_https_proxies = self.https_target
        self.max_known_proxies = settings.proxy_known_max

        # CONNECT probes remaining in the current fill cycle (reset by _fill_pools)
        self._https_probes_left = 0

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

        # Our own public IP, for transparent-proxy detection
        self._own_ip: str | None = None
        self._own_ip_lock = asyncio.Lock()

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
                stored = data.get("proxies", {})
                # Drop anything malformed left behind by earlier parsing bugs,
                # so we never waste validation cycles on entries like "http://1.2.3.4"
                self._known_proxies = {
                    p: info for p, info in stored.items() if parse_proxy_line(p) == p
                }
                dropped = len(stored) - len(self._known_proxies)
                logger.info(f"Loaded {len(self._known_proxies)} known proxies from storage")
                if dropped:
                    logger.warning(f"Discarded {dropped} malformed known proxies from storage")
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
                    "last_updated": utcnow().isoformat(),
                }, f, indent=2)
            logger.debug(f"Saved {len(self._known_proxies)} known proxies to storage")
        except Exception as e:
            logger.warning(f"Failed to save known proxies: {e}")

    def _add_known_proxy(self, proxy: str, latency: float, https_capable: bool = False) -> None:
        """Add or update a known good proxy."""
        self._known_proxies[proxy] = {
            "latency": round(latency, 2),
            "failures": 0,
            "last_success": utcnow().isoformat(),
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
            self._known_proxies[proxy]["last_success"] = utcnow().isoformat()
            self._known_proxies[proxy]["https_capable"] = https_capable
        else:
            self._add_known_proxy(proxy, latency, https_capable)

    def _sort_pool(self, pool: list[dict]) -> None:
        """Keep a pool ordered fastest-first, and restart the HTTP rotation.

        Selection reads position, not the latency field, so every write to a
        pool has to go through here — otherwise "fastest" silently means
        "whatever was appended first".

        Recent failures outrank speed. A proxy that just failed a real fetch is
        worse evidence-wise than a slow one that works, and leaving it at the
        head of the list means every caller picks it first and eats a full
        page-load timeout before falling back.
        """
        pool.sort(key=lambda x: (x.get("failures", 0), x["latency"]))
        if pool is self.http_proxies:
            self.http_index = 0

    def _add_to_pool(self, proxy: str, latency: float, https_capable: bool) -> None:
        """Add proxy to the appropriate pool."""
        proxy_info = {
            "proxy": proxy,
            "latency": round(latency, 2),
            "last_checked": utcnow().isoformat(),
            "failures": 0,
        }

        if https_capable:
            # Check if already in HTTPS pool
            existing = {p["proxy"] for p in self.https_proxies}
            if proxy not in existing and len(self.https_proxies) < self.max_https_proxies:
                self.https_proxies.append(proxy_info)
                self._sort_pool(self.https_proxies)
                logger.info(f"✓ Found HTTPS proxy: {proxy} ({latency:.0f}ms)")
        else:
            # Check if already in HTTP pool
            existing = {p["proxy"] for p in self.http_proxies}
            if proxy not in existing and len(self.http_proxies) < self.max_http_proxies:
                self.http_proxies.append(proxy_info)
                self._sort_pool(self.http_proxies)
                logger.info(f"✓ Found HTTP proxy: {proxy} ({latency:.0f}ms)")

        self._record_proxy_success(proxy, latency, https_capable)

    async def _try_known_proxies_first(self) -> tuple[int, int]:
        """Try known good proxies first. Returns (http_found, https_found)."""
        if not self._known_proxies:
            logger.info("No known proxies to try")
            return 0, 0

        # HTTPS-capable proxies are rare enough that retention beats discovery:
        # always retry every one we know, then fill up with the fastest others.
        # Sorting purely by latency hid them — a tunnel handshake is slower than
        # an HTTP fetch, so they sank below the cut every time and were never
        # retried, then aged out of the known list entirely.
        known_https = [
            item for item in self._known_proxies.items()
            if item[1].get("https_capable")
        ]
        others = sorted(
            (item for item in self._known_proxies.items() if not item[1].get("https_capable")),
            key=lambda x: x[1]["latency"],
        )
        slots = max(0, self.KNOWN_PROXIES_TO_TRY_FIRST - len(known_https))
        sorted_known = known_https + others[:slots]

        if not sorted_known:
            return 0, 0

        logger.info(f"Trying {len(sorted_known)} known proxies first...")
        http_found = 0
        https_found = 0

        for proxy, info in sorted_known:
            if info.get("https_capable"):
                # Re-check it on the protocol it is actually used for. That keeps
                # a tunnel-only proxy alive — failing it on the HTTP probe threw
                # away the scarcest thing we have, and evicted it three restarts
                # later — and gives the HTTPS pool a latency measured the same
                # way for every entry, so sorting it by speed means something.
                https_capable, latency = await self.validate_https_tunnel_timed(proxy)
                success = https_capable
            else:
                success, latency = await self.validate_proxy(proxy)
                https_capable = False
                if success:
                    https_capable, tunnel_ms = await self.validate_https_tunnel_timed(proxy)
                    if https_capable:
                        latency = tunnel_ms

            if success:
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
        """Fetch and parse proxies from a single source."""
        label = _source_label(source_url)
        proxies: set[str] = set()
        try:
            response = await client.get(source_url)
            response.raise_for_status()

            lines = response.text.splitlines()
            skipped = 0
            for line in lines:
                proxy = parse_proxy_line(line)
                if proxy:
                    proxies.add(proxy)
                elif line.strip():
                    skipped += 1

            if not proxies:
                logger.warning(f"Source {label} returned {len(lines)} lines but no usable proxies")
            elif skipped:
                logger.debug(f"Source {label}: {len(proxies)} proxies, {skipped} unparsable lines")

            if len(proxies) > self.MAX_PROXIES_PER_SOURCE:
                dropped = len(proxies) - self.MAX_PROXIES_PER_SOURCE
                proxies = set(random.sample(sorted(proxies), self.MAX_PROXIES_PER_SOURCE))
                logger.info(f"Source {label} capped at {self.MAX_PROXIES_PER_SOURCE} "
                            f"proxies ({dropped} dropped)")
        except Exception as e:
            logger.warning(f"Failed to fetch from {label}: {e}")

        return proxies

    async def _get_own_ip(self) -> str | None:
        """Our public IP without a proxy, used to detect transparent proxies.

        Cached for the process lifetime; a miss just disables that one check.
        """
        if self._own_ip is not None:
            return self._own_ip

        async with self._own_ip_lock:
            if self._own_ip is not None:
                return self._own_ip
            for url in self.VALIDATION_URLS:
                try:
                    async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                        response = await client.get(url)
                    if response.status_code != 200:
                        continue
                    match = _IPV4_RE.search(response.text[: self.MAX_RESPONSE_BYTES])
                    if match:
                        self._own_ip = match.group(0)
                        logger.info(f"Own public IP for proxy checks: {self._own_ip}")
                        return self._own_ip
                except Exception as e:
                    logger.debug(f"Own-IP lookup via {url} failed: {e}")
            logger.warning("Could not determine own public IP — "
                           "transparent-proxy detection disabled")
            return None

    async def _probe_proxy(self, proxy: str, url: str, own_ip: str | None) -> tuple[str, float]:
        """Single validation probe. Returns (verdict, latency_ms).

        Verdicts: ok | dead | slow | transparent | bad_status | bad_body
        """
        start_time = time.time()
        try:
            async with httpx.AsyncClient(
                proxy=f"http://{proxy}",
                timeout=self.VALIDATION_TIMEOUT,
                follow_redirects=False,  # a redirect means a captive portal, not a proxy
            ) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        return "bad_status", (time.time() - start_time) * 1000
                    # Cap the body: a hostile proxy must not be able to stream at us forever
                    body = b""
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) >= self.MAX_RESPONSE_BYTES:
                            break
        except Exception as e:
            logger.debug(f"Proxy probe failed for {proxy} via {url}: {e}")
            return "dead", 0.0

        latency = (time.time() - start_time) * 1000

        # The endpoint echoes the caller's IP. No IP in the body means we did not
        # reach it — an error page or interception, not a working proxy.
        match = _IPV4_RE.search(body.decode("utf-8", "replace"))
        if not match:
            return "bad_body", latency

        if own_ip and match.group(0) == own_ip:
            # Request went out under our own IP: transparent proxy, useless for scraping
            return "transparent", latency

        if latency > self.MAX_LATENCY_MS:
            return "slow", latency

        return "ok", latency

    async def validate_proxy(self, proxy: str) -> tuple[bool, float]:
        """Verify a proxy actually forwards traffic, under the latency limit.

        Checks that the response came back through the proxy (the echoed IP is
        neither missing nor our own), not merely that some HTTP 200 arrived.
        Endpoint-specific failures are retried against a second endpoint so a
        rate-limited echo service does not condemn a good proxy.
        """
        if parse_proxy_line(proxy) != proxy:
            logger.debug(f"Skipping malformed proxy entry: {proxy!r}")
            return False, 0.0

        own_ip = await self._get_own_ip()
        attempts = min(self.VALIDATION_ATTEMPTS, len(self.VALIDATION_URLS))
        urls = random.sample(self.VALIDATION_URLS, attempts)

        for url in urls:
            verdict, latency = await self._probe_proxy(proxy, url, own_ip)
            if verdict == "ok":
                return True, latency
            # Verdicts that indict the proxy itself — no point trying another endpoint
            if verdict in ("dead", "slow", "transparent"):
                logger.debug(f"Proxy {proxy} rejected: {verdict} ({latency:.0f}ms)")
                return False, latency
            # bad_status / bad_body may be the endpoint's fault; try the next one

        logger.debug(f"Proxy {proxy} rejected: no endpoint returned a usable response")
        return False, 0.0

    async def validate_https_tunnel(self, proxy: str) -> bool:
        """Test if proxy supports HTTPS CONNECT tunnel to x.com."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._validate_https_tunnel_sync, proxy)

    async def validate_https_tunnel_timed(self, proxy: str) -> tuple[bool, float]:
        """As validate_https_tunnel, but also report how long the tunnel took (ms).

        Proxies that tunnel but refuse plain HTTP have no HTTP latency to record,
        so the handshake time stands in for pool ordering and the known-good list.
        """
        start = time.monotonic()
        ok = await self.validate_https_tunnel(proxy)
        return ok, (time.monotonic() - start) * 1000

    def _validate_https_tunnel_sync(self, proxy: str) -> bool:
        """Synchronous HTTPS tunnel validation (run in executor to avoid blocking event loop).

        A CONNECT that answers 200 is not proof of a usable tunnel — some proxies
        accept CONNECT and then serve their own content. We complete a real TLS
        handshake to x.com through the tunnel before calling it HTTPS-capable.
        """
        if parse_proxy_line(proxy) != proxy:
            return False
        proxy_host, proxy_port_str = proxy.split(":")
        proxy_port = int(proxy_port_str)

        target = "x.com"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.TUNNEL_TIMEOUT)
            sock.connect((proxy_host, proxy_port))

            connect_request = (
                f"CONNECT {target}:443 HTTP/1.1\r\n"
                f"Host: {target}:443\r\n\r\n"
            )
            sock.sendall(connect_request.encode())

            # Read until the end of the response headers, bounded in size and time
            response = b""
            while b"\r\n\r\n" not in response and len(response) < self.MAX_RESPONSE_BYTES:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response += chunk

            status_line = response.split(b"\r\n", 1)[0].decode("utf-8", "replace")
            fields = status_line.split()
            # "HTTP/1.1 200 Connection established" — match the code field exactly,
            # so a "200" appearing elsewhere in the line cannot pass
            if len(fields) < 2 or fields[1] != "200":
                logger.debug(f"HTTPS tunnel refused by {proxy}: {status_line!r}")
                return False

            # Prove the tunnel carries TLS end to end
            context = ssl.create_default_context()
            with context.wrap_socket(sock, server_hostname=target) as tls_sock:
                tls_sock.do_handshake()

            logger.debug(f"HTTPS tunnel OK for {proxy}")
            return True

        except Exception as e:
            logger.debug(f"HTTPS tunnel test failed for {proxy}: {e}")
            return False
        finally:
            try:
                sock.close()
            except OSError:
                pass

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

        existing_http = {p["proxy"] for p in self.http_proxies}
        existing_https = {p["proxy"] for p in self.https_proxies}
        existing = existing_http | existing_https

        # Proxies that answer plain HTTP, with the latency we measured.
        http_latency: dict[str, float] = {}
        for proxy, (success, latency) in zip(batch, results):
            if success and proxy not in existing:
                http_latency[proxy] = latency

        # CONNECT capability is independent of plain-HTTP proxying: a proxy can
        # refuse plain HTTP and still tunnel fine. Testing only HTTP-validated
        # proxies discarded usable HTTPS proxies (measured 2026-09-02: 4 of 10
        # CONNECT-capable proxies failed the HTTP probe). So while the HTTPS pool
        # is below target, probe the whole batch — bounded by the probe budget,
        # since each probe is a socket connect plus a TLS handshake.
        want_https = len(self.https_proxies) < self.max_https_proxies
        if want_https and self._https_probes_left > 0:
            candidates = [p for p in batch if p not in existing]
            candidates = candidates[:self._https_probes_left]
        else:
            candidates = [p for p in http_latency if p not in existing]
        self._https_probes_left -= len(candidates)

        https_capable: dict[str, float] = {}
        if candidates:
            probe_results = await asyncio.gather(
                *[self.validate_https_tunnel_timed(p) for p in candidates]
            )
            for proxy, (ok, tunnel_ms) in zip(candidates, probe_results):
                if ok:
                    https_capable[proxy] = tunnel_ms

        http_found = 0
        https_found = 0

        for proxy in https_capable:
            if len(self.https_proxies) >= self.max_https_proxies:
                break
            # Always the tunnel time, never the HTTP latency: the HTTPS pool is
            # sorted fastest-first and handed out in that order, so every entry
            # has to be measured on the same protocol to be comparable.
            self._add_to_pool(proxy, https_capable[proxy], https_capable=True)
            https_found += 1

        for proxy, latency in http_latency.items():
            if proxy in https_capable:
                continue  # already placed in the HTTPS pool
            if len(self.http_proxies) >= self.max_http_proxies:
                break
            self._add_to_pool(proxy, latency, https_capable=False)
            http_found += 1

        self.last_refresh = utcnow()
        return http_found, https_found

    def _pools_filled(self) -> bool:
        """Whether a fill cycle still has work to do.

        The HTTP pool has a real minimum we expect to reach. The HTTPS pool has
        only a target: treating that target as a minimum would mean every cycle
        ends "unfilled" and re-runs at full effort forever, because on a normal
        day the internet simply does not offer 20 working CONNECT proxies. The
        per-cycle probe budget is what bounds the HTTPS search instead.
        """
        return (len(self.http_proxies) >= self.min_http_proxies and
                len(self.https_proxies) >= self.https_target)

    def _needs_more_search(self) -> bool:
        """Whether another batch in this fill cycle is still worth running."""
        if len(self.http_proxies) < self.min_http_proxies:
            return True
        # HTTPS keeps searching only while this cycle's probe budget lasts.
        # Without that bound the cycle would run its full batch allowance every
        # 10 minutes forever, because the target is one we rarely reach.
        return (len(self.https_proxies) < self.https_target
                and self._https_probes_left > 0)

    async def _fill_pools(self):
        """Search until the HTTP pool meets its minimum and the HTTPS probe
        budget for this cycle is spent."""
        max_batches = 10  # Limit search to avoid excessive CPU usage
        batches_tried = 0
        empty_batches = 0
        self._https_probes_left = self.https_probe_budget

        while self._needs_more_search() and batches_tried < max_batches:
            http_found, https_found = await self._search_batch()
            batches_tried += 1

            # A batch is only "empty" if it failed to advance something we still
            # need. Once the HTTP pool is full, http_found is 0 by construction —
            # counting that as failure used to abandon the HTTPS search after
            # three batches exactly when the HTTPS pool was the one starving.
            http_short = len(self.http_proxies) < self.min_http_proxies
            # The HTTPS sweep is bounded by the probe budget, not by yield. At
            # roughly one hit per 50 probes, three barren batches is the normal
            # case, so the empty-batch rule must not end it — that would throw
            # the rest of the budget away on every single cycle.
            https_probing = (len(self.https_proxies) < self.https_target
                             and self._https_probes_left > 0)
            made_progress = (http_found > 0 and http_short) or https_found > 0

            if not made_progress and not (http_short or https_probing):
                break  # nothing left worth searching for

            if made_progress:
                empty_batches = 0
            elif not https_probing:
                empty_batches += 1
                if empty_batches >= 3:
                    logger.info("3 consecutive empty batches, stopping search")
                    break

            # Cooldown between batches to avoid CPU spikes
            await asyncio.sleep(self.BATCH_COOLDOWN)

            logger.info(f"Pools: HTTP {len(self.http_proxies)}/{self.min_http_proxies}, "
                       f"HTTPS {len(self.https_proxies)}/{self.https_target}")

        if len(self.http_proxies) < self.min_http_proxies:
            logger.warning(f"Could not fill HTTP pool: {len(self.http_proxies)}/{self.min_http_proxies}")

        # HTTPS is best-effort: warn only when genuinely degraded, otherwise
        # report at INFO. A handful out of the target is an ordinary result.
        n_https = len(self.https_proxies)
        if n_https < self.https_floor:
            logger.warning(
                f"HTTPS pool below floor: {n_https}/{self.https_floor} "
                f"(target {self.https_target}) — X/Twitter fetches will mostly "
                "fall back to direct connections"
            )
        elif n_https < self.https_target:
            logger.info(f"HTTPS pool {n_https}/{self.https_target} (floor {self.https_floor}) — best effort")

    async def _revalidate_pool(self, pool: list[dict], pool_name: str) -> int:
        """Revalidate a pool and remove dead proxies. Returns removed count.

        The HTTPS pool is re-checked through a CONNECT tunnel, not a plain-HTTP
        fetch. Using the HTTP probe here evicted tunnel-only proxies after three
        health checks — about half an hour — which quietly undid the work of
        finding them, and it also overwrote their latency with a number measured
        on a protocol they are not used for, so the pool sorted on the wrong
        figure.
        """
        if not pool:
            return 0

        is_https = pool is self.https_proxies
        still_working = []
        removed = 0

        for proxy_info in pool:
            proxy = proxy_info["proxy"]
            if is_https:
                success, latency = await self.validate_https_tunnel_timed(proxy)
            else:
                success, latency = await self.validate_proxy(proxy)
            if success:
                proxy_info["latency"] = round(latency, 2)
                proxy_info["last_checked"] = utcnow().isoformat()
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
        self._sort_pool(pool)

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
            await self._sync_status_to_db()

            # Phase 2: Maintenance mode
            while self._running:
                await asyncio.sleep(self.REVALIDATION_INTERVAL)
                await self._revalidate_existing()

                if not self._pools_filled():
                    logger.info("Pool below minimum, refilling...")
                    await self._fill_pools()

                await self._sync_status_to_db()

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
        """Get the next HTTP proxy, walking the pool fastest to slowest.

        These callers hold no reservation, so handing every one of them the
        single fastest proxy would pile all traffic onto it and get it banned —
        the point of a pool is to spread load. Rotating over a latency-sorted
        pool gives both: each pass starts at the fastest and works down.

        The rotation restarts whenever the pool is re-sorted (a proxy added, or
        latencies refreshed by a health check), so a stale index cannot leave us
        stuck in the slow tail.
        """
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

            # Pools are kept sorted fastest-first, and `available` preserves that
            # order, so this hands out the quickest proxy not already in use.
            # Reservations spread concurrent callers down the list rather than
            # piling onto one proxy.
            proxy = available[0]
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

    async def report_result(self, proxy: str, success: bool, is_https: bool = False) -> None:
        """Record how a proxy behaved on a real fetch.

        The probes only prove a proxy answered a moment ago; a third of the
        proxies handed to the X scraper were dead or blocked by the time it used
        them (measured 2026-09-02, 8 failures in 25 checkouts over 30 minutes).
        Waiting for the 10-minute health check to notice left them sorted first,
        so every caller paid a page-load timeout on the way to the fallback.

        A single failure only demotes: an error against x.com can be that site
        blocking the exit IP rather than the proxy being dead, and evicting on
        first strike would have emptied a 12-proxy pool inside half an hour.
        Eviction needs MAX_FAILURES in a row, and any success resets the count.
        """
        async with self._lock:
            pool = self.https_proxies if is_https else self.http_proxies
            entry = next((p for p in pool if p["proxy"] == proxy), None)
            if entry is None:
                return

            if success:
                entry["failures"] = 0
                self._record_proxy_success(proxy, entry["latency"], is_https)
                self._sort_pool(pool)
                return

            failures = entry.get("failures", 0) + 1
            entry["failures"] = failures
            if failures >= self.MAX_FAILURES:
                pool.remove(entry)
                self._record_proxy_failure(proxy)
                logger.info(
                    f"✗ Dropped {'HTTPS' if is_https else 'HTTP'} proxy {proxy} "
                    f"after {failures} failed fetches"
                )
            else:
                logger.info(
                    f"↓ Demoted {'HTTPS' if is_https else 'HTTP'} proxy {proxy} "
                    f"({failures}/{self.MAX_FAILURES} failed fetches)"
                )
            self._sort_pool(pool)

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

    async def _sync_status_to_db(self) -> None:
        """Write proxy pool status to Redis (fast) with DB fallback."""
        status = {
            "http_count": len(self.http_proxies),
            "https_count": len(self.https_proxies),
            "http_min_required": self.min_http_proxies,
            "https_min_required": self.https_floor,
            "https_target": self.https_target,
            "background_running": self._running,
            "initial_fill_complete": self._initial_fill_complete,
        }
        try:
            from services.redis_client import get_redis
            r = await get_redis()
            if r is not None:
                await r.set("proxy:status", json.dumps(status))
                return
        except Exception as e:
            logger.debug(f"Redis proxy status write failed: {e}")

        # DB fallback
        try:
            from services.worker_status import write_stats
            await write_stats("proxy_manager", status)
        except Exception as e:
            logger.debug(f"Failed to sync proxy status to DB: {e}")

    def get_status(self) -> dict:
        """Get current proxy pool status."""
        return {
            "http_count": len(self.http_proxies),
            "https_count": len(self.https_proxies),
            "http_min_required": self.min_http_proxies,
            "https_min_required": self.https_floor,
            "https_target": self.https_target,
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
            "min_https_required": self.https_floor,
        }


# Singleton instance
proxy_manager = ProxyManager()
