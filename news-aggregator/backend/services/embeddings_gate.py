"""
Embeddings gate for background workers.

Policy: inside [cpu_embeddings_hours_start, cpu_embeddings_hours_end) in
cpu_embeddings_tz, embedding work is always allowed. Outside that window,
it's allowed only when gpu1 is reachable. When gpu1 is also unreachable,
the gate denies and the worker idles; the existing retry loop drains the
backlog once the gate reopens.

The pipeline (user-triggered fetches) is intentionally not gated — the
gate applies to background workers only.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings

logger = logging.getLogger(__name__)

# Cache gpu1 reachability for this many seconds to avoid hammering the host
# with a probe call on every worker iteration.
_GPU1_CACHE_TTL = 60.0

_gpu1_cache_value: bool | None = None
_gpu1_cache_at: float = 0.0

# Track the last logged decision so we only emit when state changes.
_last_logged_reason: str | None = None


def _in_window(now: datetime, start_hour: int, end_hour: int) -> bool:
    """Hour-of-day window check. Supports overnight ranges (start > end)."""
    h = now.hour
    if start_hour < end_hour:
        return start_hour <= h < end_hour
    return h >= start_hour or h < end_hour


async def _gpu1_reachable() -> bool:
    """Cached availability check against gpu1's Ollama."""
    global _gpu1_cache_value, _gpu1_cache_at

    now = time.monotonic()
    if _gpu1_cache_value is not None and (now - _gpu1_cache_at) < _GPU1_CACHE_TTL:
        return _gpu1_cache_value

    from services.gpu1_power import get_power_manager

    pm = get_power_manager()
    if pm is None:
        # WoL/power manager disabled (e.g. dev). We can't probe gpu1, so
        # treat it as unavailable — the gate then only opens during hours.
        reachable = False
    else:
        try:
            reachable = await pm.is_available()
        except Exception as e:
            logger.debug(f"gpu1 availability probe failed: {e}")
            reachable = False

    _gpu1_cache_value = reachable
    _gpu1_cache_at = now
    return reachable


def reset_cache() -> None:
    """Force the next gate call to re-probe gpu1. Test helper."""
    global _gpu1_cache_value, _gpu1_cache_at, _last_logged_reason
    _gpu1_cache_value = None
    _gpu1_cache_at = 0.0
    _last_logged_reason = None


async def embeddings_allowed() -> tuple[bool, str]:
    """
    Decide whether background embedding work may proceed right now.

    Returns (allowed, reason). Reason is a short tag suitable for logs and
    worker status: "in_hours", "out_of_hours_gpu1_up", "out_of_hours_gpu1_down".
    Transitions between reasons are logged at INFO level; steady state is silent.
    """
    global _last_logged_reason

    try:
        tz = ZoneInfo(settings.cpu_embeddings_tz)
    except Exception:
        # Bad TZ config shouldn't take the worker down — fall back to UTC and
        # log once. The window is still applied, just in the wrong timezone.
        if _last_logged_reason != "bad_tz":
            logger.warning(
                f"Invalid cpu_embeddings_tz={settings.cpu_embeddings_tz!r}, "
                "falling back to UTC for the embeddings gate."
            )
            _last_logged_reason = "bad_tz"
        tz = ZoneInfo("UTC")

    now = datetime.now(tz)
    in_window = _in_window(
        now, settings.cpu_embeddings_hours_start, settings.cpu_embeddings_hours_end
    )

    if in_window:
        reason = "in_hours"
        allowed = True
    elif await _gpu1_reachable():
        reason = "out_of_hours_gpu1_up"
        allowed = True
    else:
        reason = "out_of_hours_gpu1_down"
        allowed = False

    if reason != _last_logged_reason:
        logger.info(
            f"Embeddings gate: {reason} "
            f"(now={now.strftime('%H:%M %Z')}, "
            f"window={settings.cpu_embeddings_hours_start:02d}-"
            f"{settings.cpu_embeddings_hours_end:02d})"
        )
        _last_logged_reason = reason

    return allowed, reason
