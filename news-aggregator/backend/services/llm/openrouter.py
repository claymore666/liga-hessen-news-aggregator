"""OpenRouter LLM provider for cloud model access."""

import logging

import httpx

from .base import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider(BaseLLMProvider):
    """OpenRouter provider for cloud LLM access.

    OpenRouter provides access to multiple LLM providers through a unified API.
    See: https://openrouter.ai/

    Free tier models include:
        - mistralai/mistral-7b-instruct:free
        - meta-llama/llama-3.3-70b-instruct (with limits)
        - google/gemma-3-27b:free

    Configured via:
        - OPENROUTER_API_KEY: API key for authentication
        - OPENROUTER_MODEL: Model to use
    """

    provider_name = "openrouter"

    def __init__(
        self,
        api_key: str,
        model: str = "mistralai/mistral-7b-instruct:free",
        timeout: int = 60,
        site_url: str | None = None,
        site_name: str | None = None,
    ):
        """Initialize OpenRouter provider.

        Args:
            api_key: OpenRouter API key
            model: Model identifier
            timeout: Request timeout in seconds
            site_url: Optional site URL for rankings
            site_name: Optional site name for rankings
        """
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.site_url = site_url
        self.site_name = site_name

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate completion using OpenRouter.

        Args:
            prompt: User prompt
            system: Optional system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse with generated text
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Optional headers for OpenRouter rankings
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.site_name:
            headers["X-Title"] = self.site_name

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        if max_tokens:
            payload["max_tokens"] = max_tokens

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        usage = data.get("usage", {})

        return LLMResponse(
            text=data["choices"][0]["message"]["content"],
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
        """Check if OpenRouter is accessible.

        Returns:
            True if API key is valid and API responds, False otherwise
        """
        if not self.api_key:
            return False

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                return response.status_code == 200
        except Exception as e:
            logger.debug(f"OpenRouter not available: {e}")
            return False

    async def list_models(self) -> list[dict]:
        """List available models from OpenRouter.

        Returns:
            List of model info dicts
        """
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("data", [])
