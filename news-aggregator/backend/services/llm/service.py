"""LLM service with fallback support."""

import logging
from typing import Sequence

from .base import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class LLMService:
    """LLM service with multi-provider fallback support.

    The service tries providers in order until one succeeds.
    This allows using a local Ollama instance as primary with
    OpenRouter as a cloud fallback.

    Usage:
        providers = [
            OllamaProvider(base_url="http://gpu1:11434"),
            OpenRouterProvider(api_key="..."),
        ]
        service = LLMService(providers)

        response = await service.complete("Summarize this article...")
    """

    def __init__(self, providers: Sequence[BaseLLMProvider]):
        """Initialize LLM service with ordered providers.

        Args:
            providers: List of providers to try in order
        """
        if not providers:
            raise ValueError("At least one provider is required")
        self.providers = list(providers)

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate completion using first available provider.

        Args:
            prompt: User prompt
            system: Optional system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse from successful provider

        Raises:
            RuntimeError: If all providers fail
        """
        errors = []

        for provider in self.providers:
            try:
                logger.debug(f"Trying provider: {provider.provider_name}")
                response = await provider.complete(
                    prompt=prompt,
                    system=system,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                logger.info(f"LLM response from {provider.provider_name}")
                return response

            except Exception as e:
                error_msg = f"{provider.provider_name}: {str(e)}"
                logger.warning(f"Provider failed: {error_msg}")
                errors.append(error_msg)
                continue

        # All providers failed
        error_summary = "; ".join(errors)
        raise RuntimeError(f"All LLM providers failed: {error_summary}")

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate completion from messages using first available provider.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse from successful provider

        Raises:
            RuntimeError: If all providers fail
        """
        errors = []

        for provider in self.providers:
            try:
                logger.debug(f"Trying provider (chat): {provider.provider_name}")
                response = await provider.chat(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                logger.info(f"LLM chat response from {provider.provider_name}")
                return response

            except Exception as e:
                error_msg = f"{provider.provider_name}: {str(e)}"
                logger.warning(f"Provider chat failed: {error_msg}")
                errors.append(error_msg)
                continue

        error_summary = "; ".join(errors)
        raise RuntimeError(f"All LLM providers failed (chat): {error_summary}")

    async def check_availability(self) -> dict[str, bool]:
        """Check availability of all providers.

        Returns:
            Dict mapping provider names to availability status
        """
        result = {}
        for provider in self.providers:
            result[provider.provider_name] = await provider.is_available()
        return result

    async def get_first_available(self) -> BaseLLMProvider | None:
        """Get the first available provider.

        Returns:
            First available provider or None if none available
        """
        for provider in self.providers:
            if await provider.is_available():
                return provider
        return None
