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
        # Count consecutive 429s to escalate wait (429 responses may lack full headers)
        self._consecutive_429s: int = 0

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

    def remaining_requests(self) -> int | None:
        """Return minimum remaining requests across all tracked windows, or None if unknown."""
        if not self._limits:
            return None
        return min(info["remaining"] for info in self._limits.values())

    def seconds_until_reset(self) -> float:
        """Return seconds until the soonest exhausted window resets, or 0 if not blocked."""
        now = time.monotonic()
        min_wait = 0.0
        for limits in (self._limits, self._token_limits):
            for window, info in limits.items():
                if info["remaining"] <= 0:
                    age = now - info["updated_at"]
                    if window == "minute":
                        wait = max(0.0, 60 - age)
                    elif window == "hour":
                        wait = max(0.0, 3600 - age)
                    else:  # day
                        wait = max(0.0, 86400 - age)
                    if min_wait == 0 or (wait > 0 and wait < min_wait):
                        min_wait = wait
        return min_wait

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
        After 3+ consecutive 429s, escalates to hour window (429 responses
        often lack full headers, so minute waits loop forever when the real
        blocker is hour tokens).
        """
        self._last_429 = time.monotonic()
        self._consecutive_429s += 1
        now = time.monotonic()

        # After 3+ consecutive 429s, the minute window isn't the real blocker —
        # escalate to hour window (most likely token limit)
        if self._consecutive_429s >= 3:
            logger.warning(
                f"Cerebras: {self._consecutive_429s} consecutive 429s, "
                f"escalating to hour wait (likely token limit)"
            )
            self._token_limits["hour"] = {
                "limit": 1_000_000,
                "remaining": 0,
                "updated_at": now,
            }
            return

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

    def handle_success(self):
        """Reset consecutive 429 counter on successful request."""
        self._consecutive_429s = 0

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


# ---- Global per-key rate limiter pool ----
# Maps API key -> CerebrasRateLimiter. Survives provider recreation each worker cycle.
_key_pool: dict[str, CerebrasRateLimiter] = {}


def get_or_create_limiter(api_key: str) -> CerebrasRateLimiter:
    """Get or create a rate limiter for a specific API key."""
    if api_key not in _key_pool:
        _key_pool[api_key] = CerebrasRateLimiter()
    return _key_pool[api_key]


def get_rate_limiter() -> CerebrasRateLimiter:
    """Get the first key's rate limiter (backward compat)."""
    if _key_pool:
        return next(iter(_key_pool.values()))
    # No keys registered yet — return a fresh limiter that will be replaced
    return CerebrasRateLimiter()


def get_all_limiters() -> dict[str, CerebrasRateLimiter]:
    """Get all per-key rate limiters."""
    return _key_pool


def get_aggregated_status() -> dict | None:
    """Get aggregated rate limit status across all keys.

    Sums limits and remaining across all keys for each window.
    """
    if not _key_pool:
        return None

    # Collect individual statuses
    statuses = []
    for limiter in _key_pool.values():
        s = limiter.get_status()
        if s:
            statuses.append(s)

    if not statuses:
        return None

    # Aggregate: sum limits and remaining per window
    result = {}
    for category in ("requests", "tokens"):
        windows = {}
        for s in statuses:
            if category not in s:
                continue
            for window, info in s[category].items():
                if window not in windows:
                    windows[window] = {"limit": 0, "remaining": 0}
                windows[window]["limit"] += info["limit"] or 0
                windows[window]["remaining"] += info["remaining"] or 0
        if windows:
            result[category] = windows

    return result or None


class CerebrasProvider(BaseLLMProvider):
    """Cerebras provider for cloud LLM access.

    Supports multiple API keys for higher throughput. Each key has independent
    rate limits tracked by its own CerebrasRateLimiter. Before each request,
    the provider picks the key with the most remaining quota. If a key gets
    429'd, the next key is tried before raising.

    Cerebras free tier limits per key:
        - 30 requests/minute, 900/hour, 14,400/day
        - 64K tokens/minute, 1M/hour, 1M/day

    Configured via:
        - CEREBRAS_API_KEY: Comma-separated API keys
        - CEREBRAS_MODEL: Model to use
    """

    provider_name = "cerebras"

    def __init__(
        self,
        api_key: str | None = None,
        api_keys: list[str] | None = None,
        model: str = "gpt-oss-120b",
        timeout: int = 60,
    ):
        # Accept either single key or list of keys (backward compatible)
        if api_keys:
            keys = api_keys
        elif api_key:
            keys = [api_key]
        else:
            keys = []

        self._keys: list[tuple[str, CerebrasRateLimiter]] = [
            (k, get_or_create_limiter(k)) for k in keys
        ]
        # Round-robin index for breaking ties
        self._rr_index = 0
        self.model = model
        self.timeout = timeout

    @property
    def key_count(self) -> int:
        return len(self._keys)

    @property
    def api_key(self) -> str:
        """Return first key (backward compat for is_available)."""
        return self._keys[0][0] if self._keys else ""

    def _select_key(self) -> tuple[str, CerebrasRateLimiter]:
        """Pick the key with the most remaining requests.

        If multiple keys tie (including when none have data yet), round-robin.
        """
        if len(self._keys) == 1:
            return self._keys[0]

        best_idx = 0
        best_remaining = -1

        for i, (_, limiter) in enumerate(self._keys):
            remaining = limiter.remaining_requests()
            if remaining is None:
                # No data yet — treat as high remaining so new keys get used
                remaining = 999_999
            if remaining > best_remaining:
                best_remaining = remaining
                best_idx = i
            elif remaining == best_remaining:
                # Tie — use round-robin to break it
                if i == self._rr_index % len(self._keys):
                    best_idx = i

        self._rr_index = (best_idx + 1) % len(self._keys)
        return self._keys[best_idx]

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

    async def _chat_with_key(
        self,
        api_key: str,
        limiter: CerebrasRateLimiter,
        messages: list[dict],
        temperature: float,
        max_tokens: int | None,
    ) -> LLMResponse:
        """Execute a chat request with a specific key."""
        await limiter.wait_if_needed()

        headers = {
            "Authorization": f"Bearer {api_key}",
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
            limiter.update_from_headers(response.headers)

            if response.status_code == 429:
                limiter.handle_429()
                raise httpx.HTTPStatusError(
                    f"429 Too Many Requests",
                    request=response.request,
                    response=response,
                )

            response.raise_for_status()
            limiter.handle_success()
            data = response.json()

        key_suffix = api_key[-4:]
        usage = data.get("usage", {})
        message = data["choices"][0]["message"]
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
                "key": f"...{key_suffix}",
            },
        )

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if not self._keys:
            raise RuntimeError("No Cerebras API keys configured")

        # Try the best key first, then others on 429
        tried_keys: set[str] = set()
        last_error = None

        while len(tried_keys) < len(self._keys):
            api_key, limiter = self._select_key()

            # If we already tried this key, find another
            if api_key in tried_keys:
                found_untried = False
                for k, lim in self._keys:
                    if k not in tried_keys:
                        api_key, limiter = k, lim
                        found_untried = True
                        break
                if not found_untried:
                    break

            tried_keys.add(api_key)
            try:
                return await self._chat_with_key(
                    api_key, limiter, messages, temperature, max_tokens,
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    key_suffix = api_key[-4:]
                    remaining_keys = len(self._keys) - len(tried_keys)
                    if remaining_keys > 0:
                        logger.info(
                            f"Cerebras key ...{key_suffix} rate limited, "
                            f"trying next key ({remaining_keys} remaining)"
                        )
                    last_error = e
                    continue
                raise

        # All keys exhausted — wait on the one that resets soonest, then retry
        soonest_key, soonest_limiter = self._keys[0]
        soonest_wait = soonest_limiter.seconds_until_reset()
        for k, lim in self._keys[1:]:
            wait = lim.seconds_until_reset()
            if 0 < wait < soonest_wait or soonest_wait == 0:
                soonest_key, soonest_limiter = k, lim
                soonest_wait = wait

        if soonest_wait > 0:
            capped = min(soonest_wait, 120)
            logger.info(
                f"All {len(self._keys)} Cerebras keys exhausted, "
                f"waiting {capped:.0f}s for soonest reset"
            )
            await asyncio.sleep(capped)
            return await self._chat_with_key(
                soonest_key, soonest_limiter, messages, temperature, max_tokens,
            )

        # No wait needed but all failed — re-raise last error
        if last_error:
            raise last_error
        raise RuntimeError("All Cerebras keys exhausted")

    async def is_available(self) -> bool:
        if not self._keys:
            return False

        # Check with first key (all keys share the same account typically)
        api_key = self._keys[0][0]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://api.cerebras.ai/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                return response.status_code == 200
        except Exception as e:
            logger.debug(f"Cerebras not available: {e}")
            return False

    async def get_rate_limits(self) -> dict | None:
        """Query current rate limit usage from Cerebras API.

        Makes a minimal request per key, then returns aggregated limits.
        """
        if not self._keys:
            return None

        aggregated: dict[str, dict[str, dict]] = {}

        for api_key, limiter in self._keys:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(
                        CEREBRAS_API_URL,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.model,
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
                        },
                    )

                    limiter.update_from_headers(response.headers)

                    if response.status_code not in (200, 429):
                        continue

                    h = response.headers
                    for category, prefix in (("requests", "requests"), ("tokens", "tokens")):
                        if category not in aggregated:
                            aggregated[category] = {}
                        for window in ("minute", "hour", "day"):
                            limit = _int(h, f"x-ratelimit-limit-{prefix}-{window}")
                            remaining = _int(h, f"x-ratelimit-remaining-{prefix}-{window}")
                            if limit is not None and remaining is not None:
                                if window not in aggregated[category]:
                                    aggregated[category][window] = {"limit": 0, "remaining": 0}
                                aggregated[category][window]["limit"] += limit
                                aggregated[category][window]["remaining"] += remaining
            except Exception as e:
                logger.debug(f"Cerebras rate limits check failed for key ...{api_key[-4:]}: {e}")
                continue

        return aggregated or None


def _int(headers: httpx.Headers, key: str) -> int | None:
    """Extract an integer from response headers, or None."""
    val = headers.get(key)
    return int(val) if val is not None else None
