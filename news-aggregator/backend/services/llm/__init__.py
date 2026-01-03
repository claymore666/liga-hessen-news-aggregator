"""LLM service with multi-provider support.

This module provides a unified interface for LLM text generation
with automatic fallback between providers.

Providers:
    - OllamaProvider: Local LLM inference via Ollama
    - OpenRouterProvider: Cloud LLM access via OpenRouter API

Usage:
    from services.llm import LLMService, OllamaProvider, OpenRouterProvider

    # Create service with fallback
    service = LLMService([
        OllamaProvider(base_url="http://gpu1:11434"),
        OpenRouterProvider(api_key="sk-..."),
    ])

    # Generate completion
    response = await service.complete(
        prompt="Summarize this article...",
        system="You are a helpful assistant.",
    )
    print(response.text)
"""

from .base import BaseLLMProvider, LLMResponse
from .ollama import OllamaProvider
from .openrouter import OpenRouterProvider
from .service import LLMService

__all__ = [
    "BaseLLMProvider",
    "LLMResponse",
    "OllamaProvider",
    "OpenRouterProvider",
    "LLMService",
]
