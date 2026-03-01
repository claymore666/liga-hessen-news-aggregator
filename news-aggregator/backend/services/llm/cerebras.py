"""Cerebras LLM provider for cloud model access."""

import asyncio
import logging
import time

import httpx

from .base import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)

# Cerebras API endpoint (OpenAI-compatible)
CEREBRAS_API_URL = "https://api.cerebras.ai/v1/chat/completions"


class CerebrasRateLimiter:
    """Track Cerebras rate limits from response headers and throttle requests.

    After each API call, updates remaining counts from headers.
    Before each API call, waits if any rate limit window is exhausted.
    """

    def __init__(self):
        # Track remaining requests per window
        self._limits: dict[str, dict] = {}
        # Track remaining tokens per window
        self._token_limits: dict[str, dict] = {}
        # Track when we last got a 429 (to compute backoff)
        self._last_429: float = 0

    def update_from_headers(self, headers: httpx.Headers):
        """Update rate limit state from response headers."""
        for window in ("minute", "hour", "day"):
            limit = _int(headers, f"x-ratelimit-limit-requests-{window}")
            remaining = _int(headers, f"x-ratelimit-remaining-requests-{window}")
            if limit is not None and remaining is not None:
                self._limits[window] = {
                    "limit": limit,
                    "remaining": remaining,
                    "updated_at": time.monotonic(),
                }
            tok_limit = _int(headers, f"x-ratelimit-limit-tokens-{window}")
            tok_remaining = _int(headers, f"x-ratelimit-remaining-tokens-{window}")
            if tok_limit is not None and tok_remaining is not None:
                self._token_limits[window] = {
                    "limit": tok_limit,
                    "remaining": tok_remaining,
                    "updated_at": time.monotonic(),
                }

    async def wait_if_needed(self):
        """Wait if we're at the rate limit for any window (requests or tokens)."""
        now = time.monotonic()
        max_wait = 0
        blocking_window = None
        blocking_type = "requests"

        # Check both request and token limits
        for label, limits in (("requests", self._limits), ("tokens", self._token_limits)):
            for window, info in limits.items():
                if info["remaining"] <= 0:
                    age = now - info["updated_at"]
                    if window == "minute":
                        wait = max(0, 60 - age)
                    elif window == "hour":
                        wait = max(0, 3600 - age)
                    else:  # day
                        wait = max(0, 86400 - age)

                    if wait > max_wait:
                        max_wait = wait
                        blocking_window = window
                        blocking_type = label

        if max_wait > 0 and blocking_window:
            # Cap wait to 120s — check again after that
            capped_wait = min(max_wait, 120)
            limits = self._limits if blocking_type == "requests" else self._token_limits
            info = limits[blocking_window]
            logger.info(
                f"Cerebras rate limit ({blocking_type}/{blocking_window}): "
                f"{info['remaining']}/{info['limit']}, "
                f"waiting {capped_wait:.0f}s (full reset in {max_wait:.0f}s)"
            )
            await asyncio.sleep(capped_wait)

    def handle_429(self):
        """Record a 429 response for backoff tracking.

        Only updates windows that are already tracked (from response headers).
        If no windows are tracked yet, assumes minute window is exhausted.
        """
        self._last_429 = time.monotonic()
        now = time.monotonic()

        # Refresh updated_at for any exhausted windows (requests and tokens)
        any_exhausted = False
        for limits in (self._limits, self._token_limits):
            for window, info in limits.items():
                if info["remaining"] <= 0:
                    info["updated_at"] = now
                    any_exhausted = True

        # If no tracked window is exhausted, assume minute (most common)
        if not any_exhausted:
            self._limits["minute"] = {
                "limit": 30,
                "remaining": 0,
                "updated_at": now,
            }

    def get_status(self) -> dict | None:
        """Get current rate limit status for API responses."""
        if not self._limits and not self._token_limits:
            return None
        result = {}
        if self._limits:
            result["requests"] = {
                window: {"limit": info["limit"], "remaining": info["remaining"]}
                for window, info in self._limits.items()
            }
        if self._token_limits:
            result["tokens"] = {
                window: {"limit": info["limit"], "remaining": info["remaining"]}
                for window, info in self._token_limits.items()
            }
        return result


# Global rate limiter (shared across provider instances)
_rate_limiter = CerebrasRateLimiter()


def get_rate_limiter() -> CerebrasRateLimiter:
    """Get the global rate limiter instance."""
    return _rate_limiter


class CerebrasProvider(BaseLLMProvider):
    """Cerebras provider for cloud LLM access.

    Cerebras offers fast inference with generous free tier limits:
        - 30 requests/minute, 900/hour, 14,400/day
        - 64K tokens/minute, 1M/hour, 1M/day

    Includes built-in rate limiting: tracks remaining quota from
    response headers and waits when limits are exhausted, avoiding
    429 errors and unnecessary fallback to Ollama.

    Models include:
        - gpt-oss-120b (reasoning model, free tier)
        - qwen-3-235b-a22b-instruct-2507 (MoE, paid tier)

    Configured via:
        - CEREBRAS_API_KEY: API key for authentication
        - CEREBRAS_MODEL: Model to use
    """

    provider_name = "cerebras"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-oss-120b",
        timeout: int = 60,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._rate_limiter = _rate_limiter

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        # Wait if rate limited
        await self._rate_limiter.wait_if_needed()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                CEREBRAS_API_URL,
                headers=headers,
                json=payload,
            )

            # Update rate limits from headers (works on both 200 and 429)
            self._rate_limiter.update_from_headers(response.headers)

            if response.status_code == 429:
                self._rate_limiter.handle_429()
                # Raise so the service can retry or fall back
                response.raise_for_status()

            response.raise_for_status()
            data = response.json()

        usage = data.get("usage", {})
        message = data["choices"][0]["message"]
        # gpt-oss-120b is a reasoning model: response text is in "content",
        # chain-of-thought is in "reasoning". Fall back to "reasoning" if
        # "content" is missing/empty (e.g. when max_tokens is too low).
        text = message.get("content") or message.get("reasoning") or ""
        return LLMResponse(
            text=text,
            model=data.get("model", self.model),
            tokens_used=usage.get("total_tokens"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            metadata={
                "provider": self.provider_name,
                "id": data.get("id"),
                "finish_reason": data["choices"][0].get("finish_reason"),
            },
        )

    async def is_available(self) -> bool:
        if not self.api_key:
            return False

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://api.cerebras.ai/v1/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                return response.status_code == 200
        except Exception as e:
            logger.debug(f"Cerebras not available: {e}")
            return False

    async def get_rate_limits(self) -> dict | None:
        """Query current rate limit usage from Cerebras API.

        Makes a minimal request to read the rate limit headers.
        Returns dict with limits and remaining counts, or None on failure.
        """
        if not self.api_key:
            return None

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    CEREBRAS_API_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                    },
                )

                # Update shared rate limiter from this response too
                self._rate_limiter.update_from_headers(response.headers)

                if response.status_code not in (200, 429):
                    return None

                h = response.headers
                return {
                    "requests": {
                        "minute": {"limit": _int(h, "x-ratelimit-limit-requests-minute"),
                                   "remaining": _int(h, "x-ratelimit-remaining-requests-minute")},
                        "hour": {"limit": _int(h, "x-ratelimit-limit-requests-hour"),
                                 "remaining": _int(h, "x-ratelimit-remaining-requests-hour")},
                        "day": {"limit": _int(h, "x-ratelimit-limit-requests-day"),
                                "remaining": _int(h, "x-ratelimit-remaining-requests-day")},
                    },
                    "tokens": {
                        "minute": {"limit": _int(h, "x-ratelimit-limit-tokens-minute"),
                                   "remaining": _int(h, "x-ratelimit-remaining-tokens-minute")},
                        "hour": {"limit": _int(h, "x-ratelimit-limit-tokens-hour"),
                                 "remaining": _int(h, "x-ratelimit-remaining-tokens-hour")},
                        "day": {"limit": _int(h, "x-ratelimit-limit-tokens-day"),
                                "remaining": _int(h, "x-ratelimit-remaining-tokens-day")},
                    },
                }
        except Exception as e:
            logger.debug(f"Cerebras rate limits check failed: {e}")
            return None


def _int(headers: httpx.Headers, key: str) -> int | None:
    """Extract an integer from response headers, or None."""
    val = headers.get(key)
    return int(val) if val is not None else None
