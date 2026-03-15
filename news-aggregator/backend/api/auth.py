"""Admin authentication dependency."""

import hmac

from fastapi import Header, HTTPException

from config import settings


async def require_admin_key(x_admin_key: str = Header(default="")) -> None:
    """Verify the admin API key.

    Raises 403 if ADMIN_API_KEY is configured and the provided key doesn't match.
    If ADMIN_API_KEY is not set, all requests are allowed (backwards compatible).
    """
    if not settings.admin_api_key:
        return  # No key configured — allow all (dev/legacy mode)
    if not hmac.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=403, detail="Invalid admin key")
