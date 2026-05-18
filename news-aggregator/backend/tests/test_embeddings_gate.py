"""Tests for the background embeddings gate."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from config import settings
from services import embeddings_gate
from services.embeddings_gate import _in_window, embeddings_allowed, reset_cache


@pytest.fixture(autouse=True)
def _reset_gate_cache():
    reset_cache()
    yield
    reset_cache()


def _fixed_now(hour: int) -> datetime:
    return datetime(2026, 5, 18, hour, 0, tzinfo=ZoneInfo("Europe/Berlin"))


class TestWindow:
    @pytest.mark.parametrize(
        "hour,start,end,expected",
        [
            (7, 8, 18, False),    # before window
            (8, 8, 18, True),     # start boundary inclusive
            (12, 8, 18, True),    # mid
            (17, 8, 18, True),    # last hour included
            (18, 8, 18, False),   # end boundary exclusive
            (23, 8, 18, False),
            # Overnight window (e.g. 22-6)
            (23, 22, 6, True),
            (3, 22, 6, True),
            (6, 22, 6, False),
            (12, 22, 6, False),
        ],
    )
    def test_in_window(self, hour, start, end, expected):
        now = _fixed_now(hour)
        assert _in_window(now, start, end) is expected


class TestGateDecision:
    @pytest.mark.asyncio
    async def test_in_hours_allows_without_probing_gpu1(self):
        with patch.object(embeddings_gate, "datetime") as mock_dt:
            mock_dt.now.return_value = _fixed_now(12)
            with patch.object(embeddings_gate, "_gpu1_reachable", new=AsyncMock()) as probe:
                allowed, reason = await embeddings_allowed()
        assert allowed is True
        assert reason == "in_hours"
        probe.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_out_of_hours_with_gpu1_up_allows(self):
        with patch.object(embeddings_gate, "datetime") as mock_dt:
            mock_dt.now.return_value = _fixed_now(22)
            with patch.object(
                embeddings_gate, "_gpu1_reachable", new=AsyncMock(return_value=True)
            ):
                allowed, reason = await embeddings_allowed()
        assert allowed is True
        assert reason == "out_of_hours_gpu1_up"

    @pytest.mark.asyncio
    async def test_out_of_hours_with_gpu1_down_denies(self):
        with patch.object(embeddings_gate, "datetime") as mock_dt:
            mock_dt.now.return_value = _fixed_now(3)
            with patch.object(
                embeddings_gate, "_gpu1_reachable", new=AsyncMock(return_value=False)
            ):
                allowed, reason = await embeddings_allowed()
        assert allowed is False
        assert reason == "out_of_hours_gpu1_down"


class TestGpu1Cache:
    @pytest.mark.asyncio
    async def test_gpu1_probe_is_cached(self):
        pm = MagicMock()
        pm.is_available = AsyncMock(return_value=True)
        with patch("services.gpu1_power.get_power_manager", return_value=pm):
            r1 = await embeddings_gate._gpu1_reachable()
            r2 = await embeddings_gate._gpu1_reachable()
        assert r1 is True and r2 is True
        # Cached: second call should not have re-probed
        assert pm.is_available.await_count == 1

    @pytest.mark.asyncio
    async def test_no_power_manager_treated_as_unreachable(self):
        with patch("services.gpu1_power.get_power_manager", return_value=None):
            assert await embeddings_gate._gpu1_reachable() is False

    @pytest.mark.asyncio
    async def test_probe_exception_treated_as_unreachable(self):
        pm = MagicMock()
        pm.is_available = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("services.gpu1_power.get_power_manager", return_value=pm):
            assert await embeddings_gate._gpu1_reachable() is False


class TestConfiguredWindow:
    @pytest.mark.asyncio
    async def test_uses_settings_window(self, monkeypatch):
        monkeypatch.setattr(settings, "cpu_embeddings_hours_start", 6)
        monkeypatch.setattr(settings, "cpu_embeddings_hours_end", 10)
        with patch.object(embeddings_gate, "datetime") as mock_dt:
            mock_dt.now.return_value = _fixed_now(7)
            allowed, reason = await embeddings_allowed()
        assert allowed is True
        assert reason == "in_hours"

    @pytest.mark.asyncio
    async def test_bad_tz_falls_back_to_utc_without_raising(self, monkeypatch):
        monkeypatch.setattr(settings, "cpu_embeddings_tz", "Not/A/Zone")
        with patch.object(
            embeddings_gate, "_gpu1_reachable", new=AsyncMock(return_value=True)
        ):
            allowed, reason = await embeddings_allowed()
        assert reason in {"in_hours", "out_of_hours_gpu1_up"}
        assert allowed is True
