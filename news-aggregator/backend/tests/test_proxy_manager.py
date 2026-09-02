"""Tests for proxy manager service."""

import asyncio
import json
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

from services.proxy_manager import KNOWN_PROXIES_FILE, ProxyManager, parse_proxy_line


@pytest.fixture
def manager():
    """Create ProxyManager instance for testing."""
    with patch.object(ProxyManager, "_load_known_proxies"):
        mgr = ProxyManager()
        mgr._known_proxies = {}
    return mgr


class TestProxyManagerInit:
    """Tests for ProxyManager initialization."""

    def test_initial_state(self, manager):
        """Should initialize with correct state."""
        assert manager.http_proxies == []
        assert manager.https_proxies == []
        assert manager.http_index == 0
        assert manager.last_refresh is None
        assert manager._running is False
        assert manager._initial_fill_complete is False

    def test_has_proxy_sources(self, manager):
        """Should have proxy sources configured."""
        assert len(manager.PROXY_SOURCES) > 0
        for url in manager.PROXY_SOURCES:
            assert url.startswith("https://")

    def test_validation_settings(self, manager):
        """Should have reasonable validation settings."""
        assert manager.VALIDATION_TIMEOUT > 0
        assert manager.MAX_LATENCY_MS > 0
        assert manager.BATCH_SIZE > 0


class TestKnownProxiesPersistence:
    """Tests for known proxy persistence."""

    def test_load_known_proxies_no_file(self):
        """Should handle missing file gracefully."""
        with patch("pathlib.Path.exists", return_value=False):
            mgr = ProxyManager()
            assert mgr._known_proxies == {}

    def test_load_known_proxies_from_file(self):
        """Should load proxies from file."""
        test_data = {
            "proxies": {
                "1.2.3.4:8080": {"latency": 100, "failures": 0, "last_success": "2024-01-01"},
            },
            "last_updated": "2024-01-01"
        }

        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json.dumps(test_data))):
                mgr = ProxyManager()
                assert "1.2.3.4:8080" in mgr._known_proxies

    def test_save_known_proxies(self, manager):
        """Should save proxies to file."""
        manager._known_proxies = {"1.2.3.4:8080": {"latency": 100, "failures": 0}}

        m = mock_open()
        with patch("builtins.open", m):
            with patch("pathlib.Path.mkdir"):
                manager._save_known_proxies()

        m.assert_called_once()
        written = "".join(call.args[0] for call in m().write.call_args_list)
        assert "1.2.3.4:8080" in written

    def test_add_known_proxy(self, manager):
        """Should add proxy to known list."""
        manager._add_known_proxy("1.2.3.4:8080", 100.0)
        assert "1.2.3.4:8080" in manager._known_proxies
        assert manager._known_proxies["1.2.3.4:8080"]["latency"] == 100.0
        assert manager._known_proxies["1.2.3.4:8080"]["failures"] == 0

    def test_add_known_proxy_trims_to_max(self, manager):
        """Should trim known proxies to max size."""
        manager.max_known_proxies = 3

        # Add more than max
        for i in range(5):
            manager._add_known_proxy(f"1.2.3.{i}:8080", latency=i * 100)

        assert len(manager._known_proxies) == 3
        # Should keep lowest latency ones
        assert "1.2.3.0:8080" in manager._known_proxies


class TestProxyFailureTracking:
    """Tests for proxy failure tracking."""

    def test_record_failure_increments(self, manager):
        """Should increment failure count."""
        manager._known_proxies = {"1.2.3.4:8080": {"latency": 100, "failures": 0}}
        manager._record_proxy_failure("1.2.3.4:8080")
        assert manager._known_proxies["1.2.3.4:8080"]["failures"] == 1

    def test_record_failure_removes_after_max(self, manager):
        """Should remove proxy after max failures."""
        manager._known_proxies = {
            "1.2.3.4:8080": {"latency": 100, "failures": manager.MAX_FAILURES - 1}
        }
        manager._record_proxy_failure("1.2.3.4:8080")
        assert "1.2.3.4:8080" not in manager._known_proxies

    def test_record_failure_unknown_proxy(self, manager):
        """Should handle unknown proxy gracefully."""
        manager._record_proxy_failure("unknown:8080")  # Should not raise

    def test_record_success_resets_failures(self, manager):
        """Should reset failure count on success."""
        manager._known_proxies = {"1.2.3.4:8080": {"latency": 100, "failures": 2}}
        manager._record_proxy_success("1.2.3.4:8080", 50.0, False)
        assert manager._known_proxies["1.2.3.4:8080"]["failures"] == 0
        assert manager._known_proxies["1.2.3.4:8080"]["latency"] == 50.0

    def test_record_success_adds_new_proxy(self, manager):
        """Should add new proxy on success."""
        manager._record_proxy_success("1.2.3.4:8080", 100.0, False)
        assert "1.2.3.4:8080" in manager._known_proxies


class TestParseProxyLine:
    """Tests for proxy list line parsing."""

    @pytest.mark.parametrize("line,expected", [
        ("1.2.3.4:8080", "1.2.3.4:8080"),
        ("http://199.19.73.26:1080", "199.19.73.26:1080"),
        ("socks5://9.9.9.9:1080", "9.9.9.9:1080"),
        ("157.66.16.48:8181:Indonesia", "157.66.16.48:8181"),
        ("1.2.3.4:80  Germany elite", "1.2.3.4:80"),
        ("1.2.3.4:80,DE,elite", "1.2.3.4:80"),
        ("  8.8.8.8:443  ", "8.8.8.8:443"),
        ("1.2.3.4:8080\r", "1.2.3.4:8080"),
    ])
    def test_accepts_known_formats(self, line, expected):
        """Should normalise every format the configured sources emit."""
        assert parse_proxy_line(line) == expected

    @pytest.mark.parametrize("line", [
        "", "   ",
        "# comment: 1.2.3.4:80",
        "// note",
        "IP:PORT",
        "Country:Anonymity",
        "1.2.3.4",            # no port
        "1.2.3.4:0",          # port out of range
        "1.2.3.4:70000",      # port out of range
        "999.1.1.1:80",       # not a valid IPv4
        "1.2.3:80",           # truncated IPv4
        "example.com:8080",   # hostnames are not usable as-is
        "http://",
        "1.2.3.4:80abc",
    ])
    def test_rejects_junk(self, line):
        """Should reject anything that is not a real ip:port."""
        assert parse_proxy_line(line) is None


class _FakeStream:
    """Stand-in for an httpx streaming response."""

    def __init__(self, status=200, body=b'{"origin": "203.0.113.99"}', delay=0.0, exc=None):
        self.status_code = status
        self._body = body
        self._delay = delay
        self._exc = exc

    async def __aenter__(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_bytes(self):
        yield self._body


def _patch_client(responses):
    """Patch httpx.AsyncClient so .stream() serves `responses` in order."""
    queue = list(responses)

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, method, url):
            return queue.pop(0) if queue else _FakeStream(status=500)

    return patch("httpx.AsyncClient", _FakeClient)


@pytest.fixture
def validating_manager(manager):
    """Manager with a known own-IP so validation never touches the network."""
    manager._own_ip = "198.51.100.1"
    return manager


class TestValidateProxy:
    """Tests for proxy validation."""

    @pytest.mark.asyncio
    async def test_validate_proxy_success(self, validating_manager):
        """Should accept a proxy that echoes back a foreign IP."""
        with _patch_client([_FakeStream(body=b"203.0.113.99")]):
            success, latency = await validating_manager.validate_proxy("1.2.3.4:8080")
        assert success is True
        assert latency > 0

    @pytest.mark.asyncio
    async def test_validate_proxy_failure(self, validating_manager):
        """Should reject a proxy that cannot be reached."""
        with _patch_client([_FakeStream(exc=Exception("Connection failed"))]):
            success, latency = await validating_manager.validate_proxy("1.2.3.4:8080")
        assert success is False
        assert latency == 0.0

    @pytest.mark.asyncio
    async def test_validate_proxy_slow(self, validating_manager):
        """Should reject proxies over the latency budget."""
        validating_manager.MAX_LATENCY_MS = 0.001  # Impossibly low
        with _patch_client([_FakeStream(body=b"203.0.113.99", delay=0.01)]):
            success, _ = await validating_manager.validate_proxy("1.2.3.4:8080")
        assert success is False

    @pytest.mark.asyncio
    async def test_rejects_transparent_proxy(self, validating_manager):
        """Should reject a proxy that leaks our own IP (no anonymity)."""
        with _patch_client([_FakeStream(body=b"198.51.100.1")]):
            success, _ = await validating_manager.validate_proxy("1.2.3.4:8080")
        assert success is False

    @pytest.mark.asyncio
    async def test_rejects_body_without_ip(self, validating_manager):
        """Should reject a captive portal / error page that echoes no IP."""
        body = b"<html><body>Access denied</body></html>"
        with _patch_client([_FakeStream(body=body), _FakeStream(body=body)]):
            success, _ = await validating_manager.validate_proxy("1.2.3.4:8080")
        assert success is False

    @pytest.mark.asyncio
    async def test_rejects_redirect(self, validating_manager):
        """Should reject a redirect — a real proxy returns the response itself."""
        with _patch_client([_FakeStream(status=302, body=b""), _FakeStream(status=302, body=b"")]):
            success, _ = await validating_manager.validate_proxy("1.2.3.4:8080")
        assert success is False

    @pytest.mark.asyncio
    async def test_retries_second_endpoint(self, validating_manager):
        """Should not condemn a good proxy when one echo endpoint misbehaves."""
        with _patch_client([
            _FakeStream(status=429, body=b"rate limited"),  # endpoint's fault
            _FakeStream(body=b"203.0.113.99"),              # proxy is actually fine
        ]):
            success, _ = await validating_manager.validate_proxy("1.2.3.4:8080")
        assert success is True

    @pytest.mark.asyncio
    async def test_rejects_malformed_proxy_without_network(self, validating_manager):
        """Should reject junk entries before opening any connection."""
        with _patch_client([]):  # any stream call would yield a 500
            success, latency = await validating_manager.validate_proxy("http://1.2.3.4")
        assert success is False
        assert latency == 0.0


class TestHttpsTunnelValidation:
    """Tests for the CONNECT tunnel check."""

    def test_rejects_malformed_proxy(self, manager):
        """Should reject junk before touching a socket."""
        assert manager._validate_https_tunnel_sync("not-a-proxy") is False

    def test_rejects_unreachable_proxy(self, manager):
        """Should fail closed when the proxy refuses the connection."""
        manager.TUNNEL_TIMEOUT = 0.25
        # 192.0.2.0/24 is TEST-NET-1: guaranteed not routable
        assert manager._validate_https_tunnel_sync("192.0.2.1:9") is False


class TestRoundRobinRotation:
    """Tests for round-robin proxy rotation."""

    def test_get_next_proxy_empty(self, manager):
        """Should return None when no proxies."""
        assert manager.get_next_proxy() is None

    def test_get_next_proxy_single(self, manager):
        """Should return same proxy when only one available."""
        manager.http_proxies = [{"proxy": "1.2.3.4:8080", "latency": 100}]
        assert manager.get_next_proxy() == "1.2.3.4:8080"
        assert manager.get_next_proxy() == "1.2.3.4:8080"

    def test_get_next_proxy_rotates(self, manager):
        """Should rotate through proxies."""
        manager.http_proxies = [
            {"proxy": "1.2.3.1:8080", "latency": 100},
            {"proxy": "1.2.3.2:8080", "latency": 100},
            {"proxy": "1.2.3.3:8080", "latency": 100},
        ]

        first = manager.get_next_proxy()
        second = manager.get_next_proxy()
        third = manager.get_next_proxy()
        fourth = manager.get_next_proxy()

        assert first == "1.2.3.1:8080"
        assert second == "1.2.3.2:8080"
        assert third == "1.2.3.3:8080"
        assert fourth == "1.2.3.1:8080"  # Wraps around


class TestStatus:
    """Tests for status reporting."""

    def test_get_status_initial(self, manager):
        """Should return initial status."""
        status = manager.get_status()
        assert status["working_count"] == 0
        assert status["background_running"] is False
        assert status["initial_fill_complete"] is False
        assert "http_min_required" in status or "min_required" in status

    def test_get_status_with_proxies(self, manager):
        """Should reflect current state."""
        manager.http_proxies = [
            {"proxy": "1.2.3.4:8080", "latency": 100, "last_checked": "2024-01-01"},
        ]
        manager._running = True
        manager._initial_fill_complete = True

        status = manager.get_status()
        assert status["working_count"] == 1
        assert status["background_running"] is True
        assert status["initial_fill_complete"] is True
        assert len(status["http_proxies"]) == 1


class TestFetchProxyList:
    """Tests for proxy list fetching."""

    @pytest.mark.asyncio
    async def test_fetch_proxy_list_success(self, manager):
        """Should fetch proxies from sources."""
        proxy_list = "1.2.3.4:8080\n5.6.7.8:3128"

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = proxy_list
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            proxies = await manager.fetch_proxy_list()

            # Should have deduplicated proxies
            assert "1.2.3.4:8080" in proxies
            assert "5.6.7.8:3128" in proxies

    @pytest.mark.asyncio
    async def test_fetch_proxy_list_handles_errors(self, manager):
        """Should handle source failures gracefully."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(side_effect=Exception("Failed"))
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            proxies = await manager.fetch_proxy_list()
            assert proxies == []  # Should return empty, not raise


class TestSearchBatch:
    """Tests for batch proxy searching."""

    @pytest.mark.asyncio
    async def test_search_batch_finds_proxies(self, manager):
        """Should find and add working proxies."""
        manager._all_proxies = ["1.2.3.4:8080", "5.6.7.8:3128"]
        manager._tested_proxies = set()

        # Mock validate_proxy to return success for first proxy
        async def mock_validate(proxy):
            if proxy == "1.2.3.4:8080":
                return True, 100.0
            return False, 0.0

        # Mock validate_https_tunnel
        async def mock_https(proxy):
            return False

        with patch.object(manager, "validate_proxy", side_effect=mock_validate):
            with patch.object(manager, "validate_https_tunnel", side_effect=mock_https):
                http_found, https_found = await manager._search_batch()

        assert http_found >= 1 or https_found >= 1
        total_proxies = len(manager.http_proxies) + len(manager.https_proxies)
        assert total_proxies >= 1

    @pytest.mark.asyncio
    async def test_search_batch_fetches_fresh_when_exhausted(self, manager):
        """Should fetch fresh list when all tested."""
        manager._all_proxies = ["1.2.3.4:8080"]
        manager._tested_proxies = {"1.2.3.4:8080"}  # Already tested

        with patch.object(manager, "fetch_proxy_list", return_value=["5.6.7.8:3128"]) as mock_fetch:
            with patch.object(manager, "validate_proxy", return_value=(True, 100.0)):
                with patch.object(manager, "validate_https_tunnel", return_value=False):
                    await manager._search_batch()

        mock_fetch.assert_called_once()


class TestHttpsIndependentOfHttpProbe:
    """CONNECT capability must be tested independently of the plain-HTTP probe.

    Before 2026-09-02 only proxies that passed validate_proxy were CONNECT-tested,
    so a proxy that tunnels but refuses plain HTTP was silently discarded — measured
    at 4 of every 10 usable HTTPS proxies.
    """

    @pytest.mark.asyncio
    async def test_https_only_proxy_is_kept(self, manager):
        """A proxy that fails the HTTP probe but tunnels lands in the HTTPS pool."""
        manager._all_proxies = ["9.9.9.9:8080"]
        manager._tested_proxies = set()
        manager._https_probes_left = 10

        async def http_always_fails(proxy):
            return False, 0.0

        async def tunnel_always_works(proxy):
            return True

        with patch.object(manager, "validate_proxy", side_effect=http_always_fails):
            with patch.object(manager, "validate_https_tunnel", side_effect=tunnel_always_works):
                http_found, https_found = await manager._search_batch()

        assert https_found == 1
        assert [p["proxy"] for p in manager.https_proxies] == ["9.9.9.9:8080"]
        assert manager.http_proxies == []

    @pytest.mark.asyncio
    async def test_https_capable_proxy_not_duplicated_into_http_pool(self, manager):
        """A proxy good at both belongs in the HTTPS pool only."""
        manager._all_proxies = ["9.9.9.9:8080"]
        manager._tested_proxies = set()
        manager._https_probes_left = 10

        with patch.object(manager, "validate_proxy", return_value=(True, 100.0)):
            with patch.object(manager, "validate_https_tunnel", return_value=True):
                http_found, https_found = await manager._search_batch()

        assert (http_found, https_found) == (0, 1)
        assert len(manager.https_proxies) == 1
        assert manager.http_proxies == []

    @pytest.mark.asyncio
    async def test_probe_budget_limits_connect_tests(self, manager):
        """With no budget left, only HTTP-validated proxies are CONNECT-tested."""
        manager._all_proxies = [f"10.0.0.{i}:8080" for i in range(1, 6)]
        manager._tested_proxies = set()
        manager._https_probes_left = 0

        probed = []

        async def record(proxy):
            probed.append(proxy)
            return False

        async def http_always_fails(proxy):
            return False, 0.0

        with patch.object(manager, "validate_proxy", side_effect=http_always_fails):
            with patch.object(manager, "validate_https_tunnel", side_effect=record):
                await manager._search_batch()

        # No HTTP survivors and no budget => nothing to CONNECT-test at all.
        assert probed == []


class TestKnownHttpsRetention:
    """Known HTTPS proxies must survive a restart.

    They are the scarcest thing the pool holds — roughly one hit per 50 CONNECT
    probes — so losing one costs far more than re-probing it. Two bugs threw
    them away: sorting the known list by latency (a tunnel handshake is slower
    than an HTTP fetch, so they sank below the try-first cut and were never
    retried), and failing them on the plain-HTTP probe alone.
    """

    @pytest.mark.asyncio
    async def test_known_https_proxy_is_always_retried(self, manager):
        """A slow HTTPS proxy is tried even when faster HTTP ones crowd it out."""
        manager.KNOWN_PROXIES_TO_TRY_FIRST = 2
        manager._known_proxies = {
            "1.1.1.1:80": {"latency": 10, "failures": 0, "https_capable": False},
            "2.2.2.2:80": {"latency": 20, "failures": 0, "https_capable": False},
            "3.3.3.3:80": {"latency": 30, "failures": 0, "https_capable": False},
            "9.9.9.9:443": {"latency": 2000, "failures": 0, "https_capable": True},
        }
        tunnelled = []

        async def http_probe(proxy):
            return True, 50.0

        async def tunnel_probe(proxy):
            tunnelled.append(proxy)
            return proxy == "9.9.9.9:443"

        with patch.object(manager, "validate_proxy", side_effect=http_probe):
            with patch.object(manager, "validate_https_tunnel", side_effect=tunnel_probe):
                await manager._try_known_proxies_first()

        assert "9.9.9.9:443" in tunnelled
        assert [p["proxy"] for p in manager.https_proxies] == ["9.9.9.9:443"]

    @pytest.mark.asyncio
    async def test_known_https_proxy_survives_failed_http_probe(self, manager):
        """Refusing plain HTTP must not evict a proxy that still tunnels."""
        manager._known_proxies = {
            "9.9.9.9:443": {"latency": 2000, "failures": 2, "https_capable": True},
        }

        async def http_always_fails(proxy):
            return False, 0.0

        with patch.object(manager, "validate_proxy", side_effect=http_always_fails):
            with patch.object(manager, "validate_https_tunnel", return_value=True):
                http_found, https_found = await manager._try_known_proxies_first()

        assert (http_found, https_found) == (0, 1)
        assert [p["proxy"] for p in manager.https_proxies] == ["9.9.9.9:443"]
        # failures==2 with MAX_FAILURES==3: a recorded failure would have
        # deleted it outright.
        assert "9.9.9.9:443" in manager._known_proxies

    @pytest.mark.asyncio
    async def test_dead_known_https_proxy_still_fails(self, manager):
        """A proxy that neither proxies nor tunnels is still recorded as failed."""
        manager._known_proxies = {
            "9.9.9.9:443": {"latency": 2000, "failures": 0, "https_capable": True},
        }

        async def http_always_fails(proxy):
            return False, 0.0

        with patch.object(manager, "validate_proxy", side_effect=http_always_fails):
            with patch.object(manager, "validate_https_tunnel", return_value=False):
                http_found, https_found = await manager._try_known_proxies_first()

        assert (http_found, https_found) == (0, 0)
        assert manager.https_proxies == []
        assert manager._known_proxies["9.9.9.9:443"]["failures"] == 1


class TestFillCycleBudget:
    """A fill cycle must end when the HTTPS budget is spent, not grind on."""

    @pytest.mark.asyncio
    async def test_barren_https_batches_do_not_end_the_sweep(self, manager):
        """Three yield-free batches must not abort a sweep that still has budget."""
        manager.http_proxies = [
            {"proxy": f"1.2.3.{i}:80", "latency": 10} for i in range(manager.min_http_proxies)
        ]
        manager.https_probe_budget = 100
        batches = 0

        async def barren_batch():
            nonlocal batches
            batches += 1
            manager._https_probes_left -= 25
            return 0, 0

        with patch.object(manager, "_search_batch", side_effect=barren_batch):
            with patch("asyncio.sleep", return_value=None):
                await manager._fill_pools()

        assert batches == 4  # budget 100 / 25 per batch, not stopped at 3

    @pytest.mark.asyncio
    async def test_cycle_stops_once_budget_is_spent(self, manager):
        """With HTTP full and no budget, no batch runs at all."""
        manager.http_proxies = [
            {"proxy": f"1.2.3.{i}:80", "latency": 10} for i in range(manager.min_http_proxies)
        ]
        manager.https_probe_budget = 0
        called = False

        async def never():
            nonlocal called
            called = True
            return 0, 0

        with patch.object(manager, "_search_batch", side_effect=never):
            await manager._fill_pools()

        assert called is False


class TestFastestFirstSelection:
    """Proxies are handed out fastest-first, not at random."""

    @pytest.mark.asyncio
    async def test_checkout_returns_fastest_available(self, manager):
        manager.https_proxies = [
            {"proxy": "slow:80", "latency": 3000},
            {"proxy": "fast:80", "latency": 100},
            {"proxy": "mid:80", "latency": 900},
        ]
        manager._sort_pool(manager.https_proxies)

        picked = await manager.checkout_proxy("x_scraper", prefer_https=True)
        assert picked == "fast:80"

    @pytest.mark.asyncio
    async def test_checkout_walks_down_as_faster_ones_are_reserved(self, manager):
        """Concurrent callers spread down the list instead of contending."""
        manager.http_proxies = [
            {"proxy": "slow:80", "latency": 3000},
            {"proxy": "fast:80", "latency": 100},
            {"proxy": "mid:80", "latency": 900},
        ]
        manager._sort_pool(manager.http_proxies)

        picks = [await manager.checkout_proxy("linkedin") for _ in range(3)]
        assert picks == ["fast:80", "mid:80", "slow:80"]
        assert await manager.checkout_proxy("linkedin") is None

    @pytest.mark.asyncio
    async def test_checkin_frees_the_fastest_again(self, manager):
        manager.http_proxies = [
            {"proxy": "fast:80", "latency": 100},
            {"proxy": "mid:80", "latency": 900},
            {"proxy": "slow:80", "latency": 3000},
            {"proxy": "slowest:80", "latency": 9000},
        ]
        for _ in range(5):
            assert await manager.checkout_proxy("linkedin") == "fast:80"
            await manager.checkin_proxy("linkedin", "fast:80")

    def test_rotation_walks_fastest_to_slowest(self, manager):
        """The unreserved path spreads load, but in speed order."""
        manager.http_proxies = [
            {"proxy": "slow:80", "latency": 3000},
            {"proxy": "fast:80", "latency": 100},
            {"proxy": "mid:80", "latency": 900},
        ]
        manager._sort_pool(manager.http_proxies)

        assert [manager.get_next_proxy() for _ in range(4)] == [
            "fast:80", "mid:80", "slow:80", "fast:80",
        ]

    def test_adding_a_proxy_restarts_the_rotation(self, manager):
        """A stale index must not leave the rotation stuck in the slow tail."""
        manager.http_proxies = [
            {"proxy": "fast:80", "latency": 100},
            {"proxy": "mid:80", "latency": 900},
        ]
        manager.get_next_proxy()
        manager.get_next_proxy()
        assert manager.http_index == 2

        manager._add_to_pool("faster:80", 10.0, https_capable=False)
        assert manager.http_index == 0
        assert manager.get_next_proxy() == "faster:80"


class TestRevalidateHttpsViaTunnel:
    """The HTTPS pool must be health-checked on the protocol it is used for."""

    @pytest.mark.asyncio
    async def test_tunnel_only_proxy_survives_health_check(self, manager):
        # failures==2 with MAX_FAILURES==3: one more counted failure evicts it.
        manager.https_proxies = [{"proxy": "9.9.9.9:443", "latency": 500, "failures": 2}]

        async def http_always_fails(proxy):
            return False, 0.0

        with patch.object(manager, "validate_proxy", side_effect=http_always_fails):
            with patch.object(manager, "validate_https_tunnel", return_value=True):
                removed = await manager._revalidate_pool(manager.https_proxies, "HTTPS")

        assert removed == 0
        assert [p["proxy"] for p in manager.https_proxies] == ["9.9.9.9:443"]

    @pytest.mark.asyncio
    async def test_dead_tunnel_still_counts_as_a_failure(self, manager):
        manager.https_proxies = [{"proxy": "9.9.9.9:443", "latency": 500, "failures": 2}]

        with patch.object(manager, "validate_proxy", return_value=(True, 10.0)):
            with patch.object(manager, "validate_https_tunnel", return_value=False):
                removed = await manager._revalidate_pool(manager.https_proxies, "HTTPS")

        assert removed == 1
        assert manager.https_proxies == []


class TestRevalidateExisting:
    """Tests for revalidating existing proxies."""

    @pytest.mark.asyncio
    async def test_revalidate_keeps_working(self, manager):
        """Should keep working proxies."""
        manager.http_proxies = [
            {"proxy": "1.2.3.4:8080", "latency": 100, "last_checked": "2024-01-01"},
        ]

        with patch.object(manager, "validate_proxy", return_value=(True, 50.0)):
            http_removed, https_removed = await manager._revalidate_existing()

        assert http_removed == 0
        assert len(manager.http_proxies) == 1
        assert manager.http_proxies[0]["latency"] == 50.0  # Updated

    @pytest.mark.asyncio
    async def test_revalidate_removes_dead(self, manager):
        """Should remove dead proxies."""
        manager.http_proxies = [
            {"proxy": "1.2.3.4:8080", "latency": 100, "last_checked": "2024-01-01"},
            {"proxy": "5.6.7.8:3128", "latency": 200, "last_checked": "2024-01-01",
             "failures": manager.MAX_FAILURES - 1},
        ]

        # First proxy works, second fails
        async def mock_validate(proxy):
            if proxy == "1.2.3.4:8080":
                return True, 100.0
            return False, 0.0

        with patch.object(manager, "validate_proxy", side_effect=mock_validate):
            http_removed, https_removed = await manager._revalidate_existing()

        assert http_removed == 1
        assert len(manager.http_proxies) == 1
        assert manager.http_proxies[0]["proxy"] == "1.2.3.4:8080"


class TestBackgroundLifecycle:
    """Tests for background task lifecycle."""

    def test_start_background_search(self, manager):
        """Should create background task."""
        with patch("asyncio.create_task") as mock_create:
            mock_create.return_value = MagicMock()
            manager.start_background_search()
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_background_search(self, manager):
        """Should cancel background task."""
        # Create an actual asyncio task that we can cancel
        async def dummy():
            await asyncio.sleep(100)

        task = asyncio.create_task(dummy())
        manager._background_task = task
        manager._running = True

        await manager.stop_background_search()

        assert manager._running is False
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_stop_background_search_no_task(self, manager):
        """Should handle missing task gracefully."""
        await manager.stop_background_search()  # Should not raise


class TestManualRefresh:
    """Tests for manual proxy refresh."""

    @pytest.mark.asyncio
    async def test_refresh_clears_and_refills(self, manager):
        """Should clear pools and refill."""
        manager.http_proxies = [{"proxy": "old:8080", "latency": 100}]
        manager._tested_proxies = {"tested"}

        # Mock _fill_pools to add some proxies
        async def mock_fill():
            manager.http_proxies = [{"proxy": "new:8080", "latency": 50}]

        with patch.object(manager, "fetch_proxy_list", return_value=["new:8080"]):
            with patch.object(manager, "_fill_pools", side_effect=mock_fill):
                count = await manager.refresh_proxy_list()

        assert count == 1
        assert manager.http_proxies[0]["proxy"] == "new:8080"
        assert manager._tested_proxies == set()  # Cleared
