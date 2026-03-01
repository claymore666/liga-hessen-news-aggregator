"""Tests for Cerebras LLM provider."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.llm.cerebras import CerebrasProvider, _int


class TestCerebrasProvider:
    """Tests for CerebrasProvider."""

    def test_provider_attributes(self):
        """Provider should have correct default attributes."""
        provider = CerebrasProvider(api_key="test-key")
        assert provider.provider_name == "cerebras"
        assert provider.api_key == "test-key"
        assert provider.model == "gpt-oss-120b"
        assert provider.timeout == 60

    def test_custom_configuration(self):
        """Provider should accept custom config."""
        provider = CerebrasProvider(
            api_key="my-key",
            model="qwen-3-235b-a22b-instruct-2507",
            timeout=120,
        )
        assert provider.model == "qwen-3-235b-a22b-instruct-2507"
        assert provider.timeout == 120

    @pytest.mark.asyncio
    async def test_complete_with_content(self):
        """Complete should return content field when present."""
        provider = CerebrasProvider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Ja", "reasoning": "thinking..."}, "finish_reason": "stop"}],
            "model": "gpt-oss-120b",
            "usage": {"total_tokens": 100, "prompt_tokens": 80, "completion_tokens": 20},
        }
        mock_response.raise_for_status = MagicMock()

        with patch("services.llm.cerebras.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            response = await provider.complete("Test prompt", system="System prompt")

        assert response.text == "Ja"
        assert response.model == "gpt-oss-120b"
        assert response.tokens_used == 100
        assert response.prompt_tokens == 80
        assert response.completion_tokens == 20
        assert response.metadata["provider"] == "cerebras"
        assert response.metadata["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_complete_falls_back_to_reasoning(self):
        """Complete should fall back to reasoning field when content is missing."""
        provider = CerebrasProvider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "chatcmpl-456",
            "choices": [{"message": {"reasoning": "The answer is yes"}, "finish_reason": "length"}],
            "model": "gpt-oss-120b",
            "usage": {"total_tokens": 50, "prompt_tokens": 40, "completion_tokens": 10},
        }
        mock_response.raise_for_status = MagicMock()

        with patch("services.llm.cerebras.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            response = await provider.complete("Test prompt")

        assert response.text == "The answer is yes"

    @pytest.mark.asyncio
    async def test_complete_empty_content_falls_back_to_reasoning(self):
        """Complete should fall back to reasoning when content is empty string."""
        provider = CerebrasProvider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "chatcmpl-789",
            "choices": [{"message": {"content": "", "reasoning": "Thinking out loud"}, "finish_reason": "stop"}],
            "model": "gpt-oss-120b",
            "usage": {"total_tokens": 30, "prompt_tokens": 20, "completion_tokens": 10},
        }
        mock_response.raise_for_status = MagicMock()

        with patch("services.llm.cerebras.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            response = await provider.complete("Test prompt")

        assert response.text == "Thinking out loud"

    @pytest.mark.asyncio
    async def test_complete_no_content_no_reasoning(self):
        """Complete should return empty string when neither field is present."""
        provider = CerebrasProvider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "chatcmpl-000",
            "choices": [{"message": {}, "finish_reason": "stop"}],
            "model": "gpt-oss-120b",
            "usage": {"total_tokens": 10, "prompt_tokens": 10, "completion_tokens": 0},
        }
        mock_response.raise_for_status = MagicMock()

        with patch("services.llm.cerebras.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            response = await provider.complete("Test prompt")

        assert response.text == ""

    @pytest.mark.asyncio
    async def test_chat_sends_messages_directly(self):
        """Chat should post messages as-is to the API."""
        provider = CerebrasProvider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Response"}, "finish_reason": "stop"}],
            "model": "gpt-oss-120b",
            "usage": {"total_tokens": 50, "prompt_tokens": 30, "completion_tokens": 20},
        }
        mock_response.raise_for_status = MagicMock()

        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]

        with patch("services.llm.cerebras.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value.post = mock_post

            await provider.chat(messages, temperature=0.5, max_tokens=100)

        # Verify the payload sent to the API
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["messages"] == messages
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 100
        assert payload["model"] == "gpt-oss-120b"

    @pytest.mark.asyncio
    async def test_is_available_with_key(self):
        """is_available should return True when API responds 200."""
        provider = CerebrasProvider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("services.llm.cerebras.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await provider.is_available()

        assert result is True

    @pytest.mark.asyncio
    async def test_is_available_without_key(self):
        """is_available should return False without API key."""
        provider = CerebrasProvider(api_key="")

        result = await provider.is_available()

        assert result is False

    @pytest.mark.asyncio
    async def test_is_available_on_error(self):
        """is_available should return False on connection error."""
        provider = CerebrasProvider(api_key="test-key")

        with patch("services.llm.cerebras.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=Exception("Connection refused")
            )

            result = await provider.is_available()

        assert result is False

    @pytest.mark.asyncio
    async def test_get_rate_limits_success(self):
        """get_rate_limits should parse rate limit headers."""
        provider = CerebrasProvider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "model": "gpt-oss-120b",
            "usage": {"total_tokens": 5, "prompt_tokens": 3, "completion_tokens": 2},
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {
            "x-ratelimit-limit-requests-minute": "30",
            "x-ratelimit-remaining-requests-minute": "29",
            "x-ratelimit-limit-requests-hour": "900",
            "x-ratelimit-remaining-requests-hour": "899",
            "x-ratelimit-limit-requests-day": "14400",
            "x-ratelimit-remaining-requests-day": "14399",
            "x-ratelimit-limit-tokens-minute": "64000",
            "x-ratelimit-remaining-tokens-minute": "63990",
            "x-ratelimit-limit-tokens-hour": "1000000",
            "x-ratelimit-remaining-tokens-hour": "999990",
            "x-ratelimit-limit-tokens-day": "1000000",
            "x-ratelimit-remaining-tokens-day": "999990",
        }

        with patch("services.llm.cerebras.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await provider.get_rate_limits()

        assert result is not None
        assert result["requests"]["minute"]["limit"] == 30
        assert result["requests"]["minute"]["remaining"] == 29
        assert result["requests"]["day"]["limit"] == 14400
        assert result["tokens"]["day"]["limit"] == 1000000

    @pytest.mark.asyncio
    async def test_get_rate_limits_without_key(self):
        """get_rate_limits should return None without API key."""
        provider = CerebrasProvider(api_key="")

        result = await provider.get_rate_limits()

        assert result is None

    @pytest.mark.asyncio
    async def test_get_rate_limits_on_error(self):
        """get_rate_limits should return None on connection error."""
        provider = CerebrasProvider(api_key="test-key")

        with patch("services.llm.cerebras.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("Connection refused")
            )

            result = await provider.get_rate_limits()

        assert result is None

    @pytest.mark.asyncio
    async def test_get_rate_limits_on_429(self):
        """get_rate_limits should still parse headers on 429 rate limit response."""
        provider = CerebrasProvider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {
            "x-ratelimit-limit-requests-minute": "30",
            "x-ratelimit-remaining-requests-minute": "0",
            "x-ratelimit-limit-requests-hour": "900",
            "x-ratelimit-remaining-requests-hour": "0",
            "x-ratelimit-limit-requests-day": "14400",
            "x-ratelimit-remaining-requests-day": "100",
            "x-ratelimit-limit-tokens-minute": "64000",
            "x-ratelimit-remaining-tokens-minute": "0",
            "x-ratelimit-limit-tokens-hour": "1000000",
            "x-ratelimit-remaining-tokens-hour": "0",
            "x-ratelimit-limit-tokens-day": "1000000",
            "x-ratelimit-remaining-tokens-day": "500",
        }

        with patch("services.llm.cerebras.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await provider.get_rate_limits()

        assert result is not None
        assert result["requests"]["minute"]["remaining"] == 0
        assert result["requests"]["day"]["remaining"] == 100


class TestIntHelper:
    """Tests for _int helper function."""

    def test_valid_integer(self):
        headers = {"x-test": "42"}
        assert _int(headers, "x-test") == 42

    def test_missing_header(self):
        headers = {}
        assert _int(headers, "x-test") is None

    def test_zero_value(self):
        headers = {"x-test": "0"}
        assert _int(headers, "x-test") == 0
