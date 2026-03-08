"""Admin authentication dependency."""

from fastapi import Header, HTTPException

from config import settings


async def require_admin_key(x_admin_key: str = Header()) -> None:
    """Verify the admin API key.

    Raises 403 if ADMIN_API_KEY is configured and the provided key doesn't match.
    If ADMIN_API_KEY is not set, all requests are allowed (backwards compatible).
    """
    if not settings.admin_api_key:
        return  # No key configured — allow all (dev/legacy mode)
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")
