"""Multi-model LLM service with per-model prompts and priority fallback."""

import logging

import httpx

from .base import BaseLLMProvider, LLMResponse, RateLimitError

logger = logging.getLogger(__name__)


class MultiModelLLMService:
    """LLM service that tries multiple models in priority order.

    Each model has its own provider (OllamaProvider with different model name)
    and its own system prompt. When a model fails (429, 502, 503), the service
    falls back to the next model AND switches to that model's prompt.

    The caller passes system= but it's overridden per-model. The response
    metadata includes which model and prompt version was actually used.

    Usage:
        entries = [
            (OllamaProvider(model="gpt-oss-120b"), prompt_v9, "gpt-oss-120b", 1),
            (OllamaProvider(model="qwen3:30b"), prompt_v8, "qwen3:30b", 1),
        ]
        service = MultiModelLLMService(entries)
        response = await service.complete("Analyze this article...")
        print(response.metadata["prompt_model"])  # which model was used
    """

    def __init__(
        self,
        model_entries: list[tuple[BaseLLMProvider, str, str | None, int | None]],
    ):
        """Initialize with ordered model entries.

        Args:
            model_entries: List of (provider, system_prompt, prompt_model, prompt_version)
                          tuples ordered by priority (first = highest priority).
        """
        if not model_entries:
            raise ValueError("At least one model entry is required")
        self.model_entries = model_entries
        # Expose providers list for compatibility with LLMService interface
        self.providers = [entry[0] for entry in model_entries]

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate completion, trying models in priority order.

        The system parameter is IGNORED — each model uses its own prompt.

        Returns:
            LLMResponse with metadata including prompt_model and prompt_version.
        """
        errors = []
        all_rate_limited = True
        retry_after = None

        for provider, model_prompt, prompt_model, prompt_version in self.model_entries:
            try:
                logger.debug(f"Trying model: {provider.model} (prompt: {prompt_model} v{prompt_version})")
                response = await provider.complete(
                    prompt=prompt,
                    system=model_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                response.provider = provider.provider_name
                response.metadata["prompt_model"] = prompt_model
                response.metadata["prompt_version"] = prompt_version
                logger.info(f"LLM response from {provider.model}")
                return response

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                error_msg = f"{provider.model}: {str(e)}"
                logger.warning(f"Model failed ({status}): {error_msg}")
                errors.append(error_msg)
                if status == 429:
                    ra = e.response.headers.get("retry-after")
                    if ra:
                        try:
                            retry_after = max(retry_after or 0, float(ra))
                        except ValueError:
                            pass
                elif status in (403, 404, 500, 502, 503):
                    # 403: key banned/restricted
                    # 404: model not found on provider
                    # 500: upstream internal error
                    # 502/503: provider unavailable
                    all_rate_limited = False
                else:
                    all_rate_limited = False
                continue

            except Exception as e:
                error_msg = f"{provider.model}: {str(e)}"
                logger.warning(f"Model failed: {error_msg}")
                errors.append(error_msg)
                all_rate_limited = False
                continue

        error_summary = "; ".join(errors)
        if all_rate_limited and errors:
            raise RateLimitError(
                f"All LLM models rate-limited: {error_summary}",
                retry_after=retry_after,
            )
        raise RuntimeError(f"All LLM models failed: {error_summary}")

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate chat completion, trying models in priority order.

        For chat, the system prompt is embedded in the messages list by the caller.
        This method tries each model's provider in order without prompt substitution,
        since the conversation already contains the system message.
        """
        errors = []
        all_rate_limited = True
        retry_after = None

        for provider, _, prompt_model, prompt_version in self.model_entries:
            try:
                logger.debug(f"Trying model (chat): {provider.model}")
                response = await provider.chat(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                response.provider = provider.provider_name
                response.metadata["prompt_model"] = prompt_model
                response.metadata["prompt_version"] = prompt_version
                logger.info(f"LLM chat response from {provider.model}")
                return response

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                error_msg = f"{provider.model}: {str(e)}"
                logger.warning(f"Model chat failed ({status}): {error_msg}")
                errors.append(error_msg)
                if status == 429:
                    ra = e.response.headers.get("retry-after")
                    if ra:
                        try:
                            retry_after = max(retry_after or 0, float(ra))
                        except ValueError:
                            pass
                elif status in (403, 404, 500, 502, 503):
                    all_rate_limited = False
                else:
                    all_rate_limited = False
                continue

            except Exception as e:
                error_msg = f"{provider.model}: {str(e)}"
                logger.warning(f"Model chat failed: {error_msg}")
                errors.append(error_msg)
                all_rate_limited = False
                continue

        error_summary = "; ".join(errors)
        if all_rate_limited and errors:
            raise RateLimitError(
                f"All LLM models rate-limited (chat): {error_summary}",
                retry_after=retry_after,
            )
        raise RuntimeError(f"All LLM models failed (chat): {error_summary}")

    async def check_availability(self) -> dict[str, bool]:
        """Check availability of all model providers."""
        result = {}
        for provider, _, prompt_model, _ in self.model_entries:
            name = prompt_model or provider.model
            result[name] = await provider.is_available()
        return result

    async def get_first_available(self) -> BaseLLMProvider | None:
        """Get the first available provider."""
        for provider, _, _, _ in self.model_entries:
            if await provider.is_available():
                return provider
        return None

    async def close(self):
        """Close all providers."""
        for provider, _, _, _ in self.model_entries:
            if hasattr(provider, "close"):
                try:
                    await provider.close()
                except Exception:
                    pass
