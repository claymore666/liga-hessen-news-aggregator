"""Instagram scraper self-pacing: spacing between profile loads and the
login-wall back-off (anonymous access is rate limited per IP)."""
import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from connectors.instagram_scraper import InstagramScraperConfig, InstagramScraperConnector as C


@pytest.fixture(autouse=True)
def reset_pacing_state():
    C._gate = None
    C._last_profile_fetch = 0.0
    C._blocked_until = 0.0
    yield
    C._gate = None
    C._last_profile_fetch = 0.0
    C._blocked_until = 0.0


@pytest.mark.asyncio
async def test_backoff_skips_fetch_without_touching_browser():
    C._blocked_until = time.monotonic() + 3600
    with patch.object(C, "_fetch_paced", new=AsyncMock(return_value=["x"])) as fp:
        assert await C().fetch(InstagramScraperConfig(username="someone")) == []
    fp.assert_not_called()


@pytest.mark.asyncio
async def test_backoff_expires():
    C._blocked_until = time.monotonic() - 1
    with patch.object(C, "_fetch_paced", new=AsyncMock(return_value=["item"])):
        assert await C().fetch(InstagramScraperConfig(username="someone")) == ["item"]


@pytest.mark.asyncio
async def test_profile_loads_are_spaced_and_serialized(monkeypatch):
    monkeypatch.setattr(C, "MIN_PROFILE_SPACING", 0.3)
    starts: list[float] = []

    async def fake_fetch(self, config):
        starts.append(time.monotonic())
        await asyncio.sleep(0.05)
        return []

    monkeypatch.setattr(C, "_fetch_paced", fake_fetch)
    await asyncio.gather(*(C().fetch(InstagramScraperConfig(username=f"u{i}")) for i in range(3)))
    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 3
    assert all(g >= 0.29 for g in gaps), gaps


@pytest.mark.asyncio
async def test_login_wall_sets_backoff():
    """A redirect to /accounts/login must arm the back-off for later fetches."""
    page = AsyncMock()
    page.url = "https://www.instagram.com/accounts/login/?next=%2Fsomeone%2F"
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)

    class FakePool:
        def get_browser(self):
            class Ctx:
                async def __aenter__(self_inner):
                    return browser
                async def __aexit__(self_inner, *a):
                    return False
            return Ctx()

    with patch("connectors.instagram_scraper.browser_pool", FakePool()):
        items = await C()._fetch_with_browser(InstagramScraperConfig(username="someone"), None)
    assert items == []
    assert C._blocked_until > time.monotonic() + C.LOGIN_WALL_BACKOFF - 5
