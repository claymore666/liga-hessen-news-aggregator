"""Proxy management API endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

from services.proxy_manager import proxy_manager

router = APIRouter(prefix="/proxies", tags=["proxies"])


class ProxyResponse(BaseModel):
    """Response with a single proxy."""

    proxy: str | None


class ProxyStatus(BaseModel):
    """Proxy pool status response."""

    working_count: int
    min_required: int
    max_latency_ms: int
    proxies: list[dict]
    last_refresh: str | None
    current_index: int
    background_running: bool
    initial_fill_complete: bool
    tested_count: int
    total_available: int


class RefreshResponse(BaseModel):
    """Response from proxy refresh."""

    refreshed: int
    message: str


@router.get("/next", response_model=ProxyResponse)
def get_next_proxy() -> ProxyResponse:
    """Get next proxy from round-robin rotation.

    Returns the next working proxy or null if none available.
    """
    proxy = proxy_manager.get_next_proxy()
    return ProxyResponse(proxy=proxy)


@router.get("/status", response_model=ProxyStatus)
def get_proxy_status() -> ProxyStatus:
    """Get current proxy pool status.

    Returns count, list of working proxies with latency, and last refresh time.
    """
    status = proxy_manager.get_status()
    return ProxyStatus(**status)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_proxies() -> RefreshResponse:
    """Manually trigger proxy list refresh.

    Fetches new proxies from sources and validates them.
    This may take 30-60 seconds to complete.
    """
    count = await proxy_manager.refresh_proxy_list()
    return RefreshResponse(
        refreshed=count,
        message=f"Found {count} working proxies",
    )
