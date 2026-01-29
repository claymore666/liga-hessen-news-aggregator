"""Base LLM provider interface."""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class LLMResponse(BaseModel):
    """Response from an LLM provider."""

    text: str = Field(..., description="Generated text response")
    model: str = Field(..., description="Model that generated the response")
    tokens_used: int | None = Field(default=None, description="Total tokens used")
    prompt_tokens: int | None = Field(default=None, description="Tokens in prompt")
    completion_tokens: int | None = Field(default=None, description="Tokens in completion")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Provider-specific metadata")


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers.

    Each provider must implement:
    - complete(): Generate text completion
    - is_available(): Check if provider is accessible
    """

    # Provider metadata (override in subclass)
    provider_name: str = "base"

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a text completion.

        Args:
            prompt: The user prompt/question
            system: Optional system prompt for context
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse with generated text
        """
        pass

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a completion from a full messages list.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse with generated text
        """
        pass

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the provider is accessible.

        Returns:
            True if provider can be reached, False otherwise
        """
        pass
