"""Admin endpoints for GPU1 power management status."""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class GPU1Status(BaseModel):
    """GPU1 power management status."""

    enabled: bool
    available: bool
    was_sleeping: bool
    wake_time: Optional[str] = None
    last_activity: Optional[float] = None
    idle_time: Optional[float] = None
    auto_shutdown: bool
    idle_timeout: int
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
            was_sleeping=False,
            wake_time=None,
            last_activity=None,
            idle_time=None,
            auto_shutdown=False,
            idle_timeout=0,
            pending_shutdown=False,
            active_hours_start=0,
            active_hours_end=0,
            within_active_hours=False,
            logged_in_users=[],
            mac_address="",
            ssh_host="",
        )

    # Check current availability
    available = await power_mgr.is_available()

    # Get logged in users (only if available)
    logged_in_users: list[str] = []
    if available:
        try:
            import asyncio

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
                # Parse who output, filter out service users
                ignore_users = {power_mgr.ssh_user, "sddm"}
                for line in stdout.decode().strip().split('\n'):
                    if line.strip():
                        username = line.split()[0]
                        if username not in ignore_users:
                            logged_in_users.append(username)
                # Deduplicate
                logged_in_users = list(set(logged_in_users))

        except Exception as e:
            logger.debug(f"Failed to get logged-in users: {e}")

    # Calculate pending shutdown
    idle_time = power_mgr.get_idle_time()
    pending_shutdown = (
        power_mgr.auto_shutdown
        and power_mgr._was_sleeping
        and idle_time != float("inf")
        and idle_time >= power_mgr.idle_timeout
        and len(logged_in_users) == 0
    )

    return GPU1Status(
        enabled=True,
        available=available,
        was_sleeping=power_mgr._was_sleeping,
        wake_time=power_mgr._wake_time.isoformat() if power_mgr._wake_time else None,
        last_activity=power_mgr._last_activity,
        idle_time=idle_time if idle_time != float("inf") else None,
        auto_shutdown=power_mgr.auto_shutdown,
        idle_timeout=power_mgr.idle_timeout,
        pending_shutdown=pending_shutdown,
        active_hours_start=power_mgr.active_hours_start,
        active_hours_end=power_mgr.active_hours_end,
        within_active_hours=power_mgr.is_within_active_hours(),
        logged_in_users=logged_in_users,
        mac_address=power_mgr.mac_address,
        ssh_host=power_mgr.ssh_host,
    )
