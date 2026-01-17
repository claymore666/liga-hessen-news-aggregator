"""Tests for proxy manager service."""

import asyncio
import json
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

from services.proxy_manager import ProxyManager, KNOWN_PROXIES_FILE


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
        assert manager.working_proxies == []
        assert manager.current_index == 0
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
        manager._record_proxy_success("1.2.3.4:8080", 50.0)
        assert manager._known_proxies["1.2.3.4:8080"]["failures"] == 0
        assert manager._known_proxies["1.2.3.4:8080"]["latency"] == 50.0

    def test_record_success_adds_new_proxy(self, manager):
        """Should add new proxy on success."""
        manager._record_proxy_success("1.2.3.4:8080", 100.0)
        assert "1.2.3.4:8080" in manager._known_proxies


class TestValidateProxy:
    """Tests for proxy validation."""

    @pytest.mark.asyncio
    async def test_validate_proxy_success(self, manager):
        """Should return True for working proxy."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            success, latency = await manager.validate_proxy("1.2.3.4:8080")
            assert success is True
            assert latency > 0

    @pytest.mark.asyncio
    async def test_validate_proxy_failure(self, manager):
        """Should return False for failing proxy."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(side_effect=Exception("Connection failed"))
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            success, latency = await manager.validate_proxy("1.2.3.4:8080")
            assert success is False
            assert latency == 0.0

    @pytest.mark.asyncio
    async def test_validate_proxy_slow(self, manager):
        """Should reject slow proxies."""
        import time

        async def slow_request(*args, **kwargs):
            await asyncio.sleep(0.01)  # Small delay for test
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        # Set very low max latency to trigger rejection
        manager.MAX_LATENCY_MS = 0.001  # Impossibly low

        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = slow_request
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            success, latency = await manager.validate_proxy("1.2.3.4:8080")
            assert success is False


class TestRoundRobinRotation:
    """Tests for round-robin proxy rotation."""

    def test_get_next_proxy_empty(self, manager):
        """Should return None when no proxies."""
        assert manager.get_next_proxy() is None

    def test_get_next_proxy_single(self, manager):
        """Should return same proxy when only one available."""
        manager.working_proxies = [{"proxy": "1.2.3.4:8080", "latency": 100}]
        assert manager.get_next_proxy() == "1.2.3.4:8080"
        assert manager.get_next_proxy() == "1.2.3.4:8080"

    def test_get_next_proxy_rotates(self, manager):
        """Should rotate through proxies."""
        manager.working_proxies = [
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
        assert "min_required" in status

    def test_get_status_with_proxies(self, manager):
        """Should reflect current state."""
        manager.working_proxies = [
            {"proxy": "1.2.3.4:8080", "latency": 100, "last_checked": "2024-01-01"},
        ]
        manager._running = True
        manager._initial_fill_complete = True

        status = manager.get_status()
        assert status["working_count"] == 1
        assert status["background_running"] is True
        assert status["initial_fill_complete"] is True
        assert len(status["proxies"]) == 1


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

        with patch.object(manager, "validate_proxy", side_effect=mock_validate):
            found = await manager._search_batch()

        assert found == 1
        assert len(manager.working_proxies) == 1
        assert manager.working_proxies[0]["proxy"] == "1.2.3.4:8080"

    @pytest.mark.asyncio
    async def test_search_batch_fetches_fresh_when_exhausted(self, manager):
        """Should fetch fresh list when all tested."""
        manager._all_proxies = ["1.2.3.4:8080"]
        manager._tested_proxies = {"1.2.3.4:8080"}  # Already tested

        with patch.object(manager, "fetch_proxy_list", return_value=["5.6.7.8:3128"]) as mock_fetch:
            with patch.object(manager, "validate_proxy", return_value=(True, 100.0)):
                await manager._search_batch()

        mock_fetch.assert_called_once()


class TestRevalidateExisting:
    """Tests for revalidating existing proxies."""

    @pytest.mark.asyncio
    async def test_revalidate_keeps_working(self, manager):
        """Should keep working proxies."""
        manager.working_proxies = [
            {"proxy": "1.2.3.4:8080", "latency": 100, "last_checked": "2024-01-01"},
        ]

        with patch.object(manager, "validate_proxy", return_value=(True, 50.0)):
            removed = await manager._revalidate_existing()

        assert removed == 0
        assert len(manager.working_proxies) == 1
        assert manager.working_proxies[0]["latency"] == 50.0  # Updated

    @pytest.mark.asyncio
    async def test_revalidate_removes_dead(self, manager):
        """Should remove dead proxies."""
        manager.working_proxies = [
            {"proxy": "1.2.3.4:8080", "latency": 100, "last_checked": "2024-01-01"},
            {"proxy": "5.6.7.8:3128", "latency": 200, "last_checked": "2024-01-01"},
        ]

        # First proxy works, second fails
        async def mock_validate(proxy):
            if proxy == "1.2.3.4:8080":
                return True, 100.0
            return False, 0.0

        with patch.object(manager, "validate_proxy", side_effect=mock_validate):
            removed = await manager._revalidate_existing()

        assert removed == 1
        assert len(manager.working_proxies) == 1
        assert manager.working_proxies[0]["proxy"] == "1.2.3.4:8080"


class TestBackgroundLifecycle:
    """Tests for background task lifecycle."""

    def test_start_background_search(self, manager):
        """Should create background task."""
        with patch("asyncio.create_task") as mock_create:
            mock_create.return_value = MagicMock()
            manager.start_background_search()
            mock_create.assert_called_once()

    def test_stop_background_search(self, manager):
        """Should cancel background task."""
        mock_task = MagicMock()
        mock_task.done.return_value = False
        manager._background_task = mock_task
        manager._running = True

        manager.stop_background_search()

        assert manager._running is False
        mock_task.cancel.assert_called_once()

    def test_stop_background_search_no_task(self, manager):
        """Should handle missing task gracefully."""
        manager.stop_background_search()  # Should not raise


class TestManualRefresh:
    """Tests for manual proxy refresh."""

    @pytest.mark.asyncio
    async def test_refresh_clears_and_refills(self, manager):
        """Should clear pool and refill."""
        manager.working_proxies = [{"proxy": "old:8080", "latency": 100}]
        manager._tested_proxies = {"tested"}

        # Mock fill_pool to add some proxies
        async def mock_fill():
            manager.working_proxies = [{"proxy": "new:8080", "latency": 50}]

        with patch.object(manager, "fetch_proxy_list", return_value=["new:8080"]):
            with patch.object(manager, "_fill_pool", side_effect=mock_fill):
                count = await manager.refresh_proxy_list()

        assert count == 1
        assert manager.working_proxies[0]["proxy"] == "new:8080"
        assert manager._tested_proxies == set()  # Cleared
