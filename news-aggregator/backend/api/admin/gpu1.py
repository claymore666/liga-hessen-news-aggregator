"""Admin endpoints for GPU1 power management status."""

import logging
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class GPU1Status(BaseModel):
    """GPU1 power management status."""

    enabled: bool
    available: bool  # Host reachable (SSH port)
    ollama_available: bool  # Ollama API reachable
    was_sleeping: bool | None = None  # None when unavailable
    wake_time: str | None = None
    last_activity: float | None = None
    idle_time: float | None = None
    auto_shutdown: bool
    idle_timeout: int
    wake_interval: int
    last_wol_time: float | None = None
    seconds_until_next_wake: int | None = None
    pending_shutdown: bool
    active_hours_start: int
    active_hours_end: int
    within_active_hours: bool
    logged_in_users: list[str]
    mac_address: str
    ssh_host: str


@router.get("/admin/gpu1/status", response_model=GPU1Status)
async def get_gpu1_status() -> GPU1Status:
    """Get GPU1 power management status.

    Returns current state of Wake-on-LAN management including:
    - Whether gpu1 is available (Ollama reachable)
    - Wake/sleep state tracking
    - Auto-shutdown status
    - Active hours configuration
    - Logged-in users
    """
    from services.gpu1_power import get_power_manager

    power_mgr = get_power_manager()

    if power_mgr is None:
        return GPU1Status(
            enabled=False,
            available=False,
            ollama_available=False,
            was_sleeping=False,
            wake_time=None,
            last_activity=None,
            idle_time=None,
            auto_shutdown=False,
            idle_timeout=0,
            wake_interval=0,
            last_wol_time=None,
            seconds_until_next_wake=None,
            pending_shutdown=False,
            active_hours_start=0,
            active_hours_end=0,
            within_active_hours=False,
            logged_in_users=[],
            mac_address="",
            ssh_host="",
        )

    import asyncio

    async def check_logged_in_users() -> list[str]:
        """Get logged-in users via SSH."""
        try:
            cmd = [
                "ssh",
                "-i", power_mgr.ssh_key_path,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=5",
                "-o", "BatchMode=yes",
                f"{power_mgr.ssh_user}@{power_mgr.ssh_host}",
                "who",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)

            if proc.returncode == 0:
                ignore_users = {power_mgr.ssh_user, "sddm"}
                users = []
                for line in stdout.decode().strip().split('\n'):
                    if line.strip():
                        username = line.split()[0]
                        if username not in ignore_users:
                            users.append(username)
                return list(set(users))
        except Exception as e:
            logger.debug(f"Failed to get logged-in users: {e}")
        return []

    # Run all checks concurrently
    host_reachable, ollama_available, logged_in_users = await asyncio.gather(
        power_mgr.is_host_reachable(),
        power_mgr.is_available(),
        check_logged_in_users(),
    )

    # Host is available if reachable OR Ollama responds (Ollama implies host is up)
    available = host_reachable or ollama_available

    # If gpu1 host is not reachable, discard SSH results (may be stale)
    if not available:
        logged_in_users = []

    # Calculate pending shutdown
    idle_time = power_mgr.get_idle_time()
    pending_shutdown = (
        power_mgr.auto_shutdown
        and power_mgr._was_sleeping
        and idle_time != float("inf")
        and idle_time >= power_mgr.idle_timeout
        and len(logged_in_users) == 0
    )

    # Get wake interval status
    last_wol_time = power_mgr._last_wol_time
    seconds_until = power_mgr._seconds_until_next_wake(last_wol_time)

    return GPU1Status(
        enabled=True,
        available=available,
        ollama_available=ollama_available,
        # Only show wake state when gpu1 is available; otherwise it's meaningless
        was_sleeping=power_mgr._was_sleeping if available else None,
        wake_time=power_mgr._wake_time.isoformat() if available and power_mgr._wake_time else None,
        last_activity=power_mgr._last_activity if available else None,
        idle_time=idle_time if available and idle_time != float("inf") else None,
        auto_shutdown=power_mgr.auto_shutdown,
        idle_timeout=power_mgr.idle_timeout,
        wake_interval=power_mgr.wake_interval,
        last_wol_time=last_wol_time,
        seconds_until_next_wake=seconds_until if seconds_until > 0 else None,
        pending_shutdown=pending_shutdown,
        active_hours_start=power_mgr.active_hours_start,
        active_hours_end=power_mgr.active_hours_end,
        within_active_hours=power_mgr.is_within_active_hours(),
        logged_in_users=logged_in_users,
        mac_address=power_mgr.mac_address,
        ssh_host=power_mgr.ssh_host,
    )
