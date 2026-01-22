"""
GPU1 Power Management with Wake-on-LAN support.

Manages gpu1 power state for LLM processing:
- Detects when gpu1 is sleeping (Ollama unreachable)
- Sends Wake-on-LAN magic packet to wake it
- Polls until Ollama is available
- Optionally shuts down after idle period if we woke it

Configuration via environment:
- GPU1_WOL_ENABLED: Enable/disable WoL feature (default: true)
- GPU1_MAC_ADDRESS: MAC address for WoL packets
- GPU1_BROADCAST: Broadcast address for WoL (default: 255.255.255.255)
- GPU1_SSH_HOST: SSH host for shutdown command
- GPU1_SSH_USER: SSH user for shutdown command
- GPU1_SSH_KEY_PATH: Path to SSH private key
- GPU1_AUTO_SHUTDOWN: Auto-shutdown after idle if we woke it (default: true)
- GPU1_IDLE_TIMEOUT: Seconds idle before auto-shutdown (default: 300)
- GPU1_WAKE_TIMEOUT: Max seconds to wait for Ollama after WoL (default: 120)
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class GPU1PowerManager:
    """Manages gpu1 power state for LLM processing."""

    def __init__(
        self,
        mac_address: str,
        ollama_url: str,
        broadcast: str = "255.255.255.255",
        ssh_host: str = "192.168.0.141",
        ssh_user: str = "ligahessen",
        ssh_key_path: str = "/app/ssh/id_ed25519",
        auto_shutdown: bool = True,
        idle_timeout: int = 300,
        wake_timeout: int = 120,
        active_hours_start: int = 7,
        active_hours_end: int = 16,
        active_weekdays_only: bool = True,
    ):
        """
        Initialize GPU1 power manager.

        Args:
            mac_address: MAC address for WoL packets (format: xx:xx:xx:xx:xx:xx)
            ollama_url: Full Ollama URL (e.g., http://gpu1:11434)
            broadcast: Broadcast address for WoL packets
            ssh_host: SSH host for shutdown command
            ssh_user: SSH user for shutdown command
            ssh_key_path: Path to SSH private key for shutdown
            auto_shutdown: Whether to shutdown after idle if we woke it
            idle_timeout: Seconds idle before auto-shutdown
            wake_timeout: Max seconds to wait after WoL
            active_hours_start: Hour (0-23) when gpu1 usage is allowed
            active_hours_end: Hour (0-23) when gpu1 usage stops
            active_weekdays_only: Only wake on weekdays (Mon-Fri)
        """
        self.mac_address = mac_address
        self.ollama_url = ollama_url.rstrip("/")
        self.broadcast = broadcast
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.ssh_key_path = ssh_key_path
        self.auto_shutdown = auto_shutdown
        self.idle_timeout = idle_timeout
        self.wake_timeout = wake_timeout
        self.active_hours_start = active_hours_start
        self.active_hours_end = active_hours_end
        self.active_weekdays_only = active_weekdays_only

        # State tracking
        self._was_sleeping = False
        self._wake_time: Optional[datetime] = None
        self._last_activity: Optional[float] = None

    @property
    def was_sleeping(self) -> bool:
        """Whether gpu1 was sleeping when we last checked."""
        return self._was_sleeping

    def is_within_active_hours(self) -> bool:
        """
        Check if current time is within the allowed active hours.

        Returns:
            True if gpu1 usage is allowed now, False otherwise
        """
        now = datetime.now()
        current_hour = now.hour

        # Check weekday restriction (Monday=0, Sunday=6)
        if self.active_weekdays_only and now.weekday() >= 5:
            return False

        # Handle same-day range (e.g., 7-16)
        if self.active_hours_start < self.active_hours_end:
            return self.active_hours_start <= current_hour < self.active_hours_end

        # Handle overnight range (e.g., 22-6)
        return current_hour >= self.active_hours_start or current_hour < self.active_hours_end

    async def is_available(self) -> bool:
        """
        Check if Ollama is reachable on gpu1.

        Returns:
            True if Ollama responds, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.ollama_url}/api/tags")
                return response.status_code == 200
        except Exception as e:
            logger.debug(f"Ollama not available at {self.ollama_url}: {e}")
            return False

    async def wake(self) -> bool:
        """
        Send Wake-on-LAN magic packet to gpu1.

        Returns:
            True if packet sent successfully, False otherwise
        """
        try:
            from wakeonlan import send_magic_packet

            send_magic_packet(
                self.mac_address,
                ip_address=self.broadcast,
                port=9,
            )

            self._was_sleeping = True
            self._wake_time = datetime.utcnow()

            logger.info(
                f"Sent WoL packet to {self.mac_address} via {self.broadcast}:9"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to send WoL packet: {e}")
            return False

    async def wait_for_ready(self, timeout: Optional[int] = None) -> bool:
        """
        Poll Ollama until available or timeout.

        Args:
            timeout: Max seconds to wait (default: self.wake_timeout)

        Returns:
            True if Ollama became available, False on timeout
        """
        timeout = timeout or self.wake_timeout
        start = time.time()
        poll_interval = 5  # seconds between checks

        logger.info(f"Waiting up to {timeout}s for Ollama to become available...")

        while (time.time() - start) < timeout:
            if await self.is_available():
                elapsed = time.time() - start
                logger.info(f"Ollama available after {elapsed:.1f}s")
                return True

            await asyncio.sleep(poll_interval)
            logger.info(
                f"Still waiting for gpu1 Ollama... ({time.time() - start:.0f}s elapsed)"
            )

        logger.warning(f"Timeout after {timeout}s waiting for Ollama")
        return False

    async def ensure_available(self) -> bool:
        """
        Ensure gpu1 is available, waking if needed.

        This is the main entry point for LLM worker integration.
        Checks availability, sends WoL if needed, waits for ready.

        Active hours only restrict WAKING gpu1 - if gpu1 is already awake,
        it will be used regardless of the time.

        Returns:
            True if Ollama is available, False if wake/wait failed or outside active hours
        """
        # First check if already available - use it regardless of active hours
        if await self.is_available():
            logger.debug("gpu1 already available")
            return True

        # gpu1 is not available - if we previously woke it, it was shut down externally
        if self._was_sleeping:
            logger.info("gpu1 went down (external shutdown), resetting wake state")
            self.reset_state()

        # Check if we're allowed to wake it
        if not self.is_within_active_hours():
            now = datetime.now()
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            weekday_str = f" ({day_names[now.weekday()]})" if self.active_weekdays_only else ""
            weekday_restriction = " Mon-Fri" if self.active_weekdays_only else ""
            logger.info(
                f"gpu1 not available and outside active hours "
                f"(current: {now.hour}:00{weekday_str}, allowed: {self.active_hours_start}:00-{self.active_hours_end}:00{weekday_restriction}). "
                f"Skipping WoL, items will be queued."
            )
            return False

        # Within active hours, try to wake
        logger.info("gpu1 not available, sending Wake-on-LAN...")

        if not await self.wake():
            logger.error("Failed to send WoL packet")
            return False

        # Wait for Ollama to come up
        if await self.wait_for_ready():
            logger.info("gpu1 woken and ready for LLM processing")
            return True

        logger.error("gpu1 did not respond after WoL")
        return False

    async def shutdown(self) -> bool:
        """
        Shutdown gpu1 via SSH.

        Sends 'sudo shutdown -h now' via SSH using key authentication.

        Returns:
            True if command executed successfully, False otherwise
        """
        try:
            cmd = [
                "ssh",
                "-i", self.ssh_key_path,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                "-o", "BatchMode=yes",
                f"{self.ssh_user}@{self.ssh_host}",
                "sudo", "shutdown", "-h", "now",
            ]

            logger.info(f"Shutting down gpu1 via SSH ({self.ssh_user}@{self.ssh_host})")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

            if proc.returncode == 0:
                logger.info("gpu1 shutdown command sent successfully")
                return True

            # Return code 255 is expected when connection drops during shutdown
            if proc.returncode == 255:
                logger.info("gpu1 shutdown initiated (connection closed)")
                return True

            logger.warning(
                f"Shutdown command returned {proc.returncode}: "
                f"stdout={stdout.decode()}, stderr={stderr.decode()}"
            )
            return False

        except asyncio.TimeoutError:
            logger.warning("Shutdown command timed out (may have worked)")
            return True  # Shutdown often drops connection before returning

        except Exception as e:
            logger.error(f"Failed to shutdown gpu1: {e}")
            return False

    async def has_other_users(self) -> bool:
        """
        Check if users other than ligahessen are logged into gpu1.

        Returns:
            True if other users are logged in, False if only ligahessen or no users
        """
        try:
            cmd = [
                "ssh",
                "-i", self.ssh_key_path,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                "-o", "BatchMode=yes",
                f"{self.ssh_user}@{self.ssh_host}",
                "who",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

            if proc.returncode != 0:
                logger.warning(f"Failed to check users on gpu1: {stderr.decode()}")
                return True  # Assume users present on error (safe default)

            # Parse who output - each line is a logged in user
            # Format: username tty date time (ip)
            # Ignore: ligahessen (our service user), sddm (display manager, always running)
            ignore_users = {self.ssh_user, "sddm"}
            lines = stdout.decode().strip().split('\n')
            other_users = []
            for line in lines:
                if not line.strip():
                    continue
                username = line.split()[0]
                if username not in ignore_users:
                    other_users.append(username)

            if other_users:
                unique_users = list(set(other_users))
                logger.info(
                    f"Users logged into gpu1: {', '.join(unique_users)} - skipping shutdown"
                )
                return True

            return False

        except asyncio.TimeoutError:
            logger.warning("Timeout checking gpu1 users, assuming users present")
            return True

        except Exception as e:
            logger.warning(f"Error checking gpu1 users: {e}")
            return True  # Assume users present on error (safe default)

    def record_activity(self):
        """Record that LLM processing activity occurred."""
        self._last_activity = time.time()

    def get_idle_time(self) -> float:
        """
        Get seconds since last activity.

        Returns:
            Seconds since last activity, or infinity if never active
        """
        if self._last_activity is None:
            return float("inf")
        return time.time() - self._last_activity

    async def shutdown_if_idle(self) -> bool:
        """
        Shutdown gpu1 if we woke it and it's been idle.

        Only shuts down if:
        - auto_shutdown is enabled
        - We woke gpu1 (was_sleeping is True)
        - Idle time exceeds idle_timeout
        - No other users are logged in (only ligahessen or no users)

        Returns:
            True if shutdown was triggered, False otherwise
        """
        if not self.auto_shutdown:
            return False

        if not self._was_sleeping:
            return False

        idle_time = self.get_idle_time()
        if idle_time < self.idle_timeout:
            logger.debug(
                f"gpu1 idle for {idle_time:.0f}s, "
                f"threshold is {self.idle_timeout}s"
            )
            return False

        # Check if other users are logged in before shutdown
        if await self.has_other_users():
            logger.debug("Skipping shutdown due to other users on gpu1")
            return False

        logger.info(
            f"gpu1 idle for {idle_time:.0f}s (>{self.idle_timeout}s), "
            "no other users logged in, shutting down..."
        )

        if await self.shutdown():
            self.reset_state()
            return True

        return False

    def reset_state(self):
        """Reset wake state tracking."""
        self._was_sleeping = False
        self._wake_time = None
        self._last_activity = None

    def get_status(self) -> dict:
        """Get current power manager status."""
        return {
            "was_sleeping": self._was_sleeping,
            "wake_time": self._wake_time.isoformat() if self._wake_time else None,
            "last_activity": self._last_activity,
            "idle_time": self.get_idle_time() if self._last_activity else None,
            "auto_shutdown": self.auto_shutdown,
            "idle_timeout": self.idle_timeout,
        }


# Global instance
_power_manager: Optional[GPU1PowerManager] = None


def get_power_manager() -> Optional[GPU1PowerManager]:
    """
    Get the global GPU1 power manager instance.

    Creates the instance on first call if WoL is enabled.

    Returns:
        GPU1PowerManager instance if enabled, None otherwise
    """
    global _power_manager

    if _power_manager is not None:
        return _power_manager

    from config import settings

    if not settings.gpu1_wol_enabled:
        logger.debug("GPU1 WoL disabled")
        return None

    _power_manager = GPU1PowerManager(
        mac_address=settings.gpu1_mac_address,
        ollama_url=settings.ollama_base_url,
        broadcast=settings.gpu1_broadcast,
        ssh_host=settings.gpu1_ssh_host,
        ssh_user=settings.gpu1_ssh_user,
        ssh_key_path=settings.gpu1_ssh_key_path,
        auto_shutdown=settings.gpu1_auto_shutdown,
        idle_timeout=settings.gpu1_idle_timeout,
        wake_timeout=settings.gpu1_wake_timeout,
        active_hours_start=settings.gpu1_active_hours_start,
        active_hours_end=settings.gpu1_active_hours_end,
        active_weekdays_only=settings.gpu1_active_weekdays_only,
    )

    weekdays_str = " (Mon-Fri only)" if settings.gpu1_active_weekdays_only else ""
    logger.info(
        f"GPU1 power manager initialized: "
        f"MAC={settings.gpu1_mac_address}, "
        f"active_hours={settings.gpu1_active_hours_start}:00-{settings.gpu1_active_hours_end}:00{weekdays_str}, "
        f"broadcast={settings.gpu1_broadcast}, "
        f"auto_shutdown={settings.gpu1_auto_shutdown}"
    )

    return _power_manager


def reset_power_manager():
    """Reset the global power manager (for testing)."""
    global _power_manager
    _power_manager = None
