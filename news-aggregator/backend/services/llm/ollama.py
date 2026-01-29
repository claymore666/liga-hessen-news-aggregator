"""Ollama LLM provider for local model inference."""

import logging

import httpx

from .base import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):
    """Ollama provider for local LLM inference.

    Ollama runs models locally and provides an API compatible interface.
    See: https://ollama.ai/

    Configured via:
        - OLLAMA_BASE_URL: API endpoint (default: http://localhost:11434)
        - OLLAMA_MODEL: Model to use (default: llama3.2)
    """

    provider_name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2",
        timeout: int = 120,
    ):
        """Initialize Ollama provider.

        Args:
            base_url: Ollama API base URL
            model: Model name to use
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate completion using Ollama.

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

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,  # Disable qwen3 thinking mode to ensure content is returned
            "options": {
                "temperature": temperature,
            },
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content = data["message"]["content"]
        # qwen3 models may use thinking mode where response is in 'thinking' field
        # If content is empty but thinking exists, the model didn't produce output
        if not content and data["message"].get("thinking"):
            logger.warning(f"Ollama returned empty content with thinking mode active")

        return LLMResponse(
            text=content,
            model=self.model,
            tokens_used=data.get("eval_count"),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            metadata={
                "provider": self.provider_name,
                "total_duration": data.get("total_duration"),
                "load_duration": data.get("load_duration"),
                "has_thinking": bool(data["message"].get("thinking")),
            },
        )

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate completion from a full messages list.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse with generated text
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
            },
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content = data["message"]["content"]
        if not content and data["message"].get("thinking"):
            logger.warning("Ollama chat() returned empty content with thinking mode active")

        return LLMResponse(
            text=content,
            model=self.model,
            tokens_used=data.get("eval_count"),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            metadata={
                "provider": self.provider_name,
                "total_duration": data.get("total_duration"),
                "load_duration": data.get("load_duration"),
                "has_thinking": bool(data["message"].get("thinking")),
            },
        )

    async def is_available(self) -> bool:
        """Check if Ollama is accessible.

        Returns:
            True if Ollama API responds, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except Exception as e:
            logger.debug(f"Ollama not available: {e}")
            return False

    async def list_models(self) -> list[str]:
        """List available models in Ollama.

        Returns:
            List of model names
        """
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
