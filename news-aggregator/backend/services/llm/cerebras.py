"""Cerebras LLM provider for cloud model access."""

import logging

import httpx

from .base import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)

# Cerebras API endpoint (OpenAI-compatible)
CEREBRAS_API_URL = "https://api.cerebras.ai/v1/chat/completions"


class CerebrasProvider(BaseLLMProvider):
    """Cerebras provider for cloud LLM access.

    Cerebras offers fast inference with generous free tier limits:
        - 14,400 requests/day
        - 1M tokens/day

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
