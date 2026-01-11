"""Tests for LLM services."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.llm import (
    BaseLLMProvider,
    LLMResponse,
    LLMService,
    OllamaProvider,
    OpenRouterProvider,
)


# === LLMResponse Tests ===


class TestLLMResponse:
    """Tests for LLMResponse model."""

    def test_response_with_required_fields(self):
        """Response should work with required fields only."""
        response = LLMResponse(text="Hello", model="test-model")
        assert response.text == "Hello"
        assert response.model == "test-model"
        assert response.tokens_used is None

    def test_response_with_all_fields(self):
        """Response should work with all fields."""
        response = LLMResponse(
            text="Generated text",
            model="llama3.2",
            tokens_used=100,
            prompt_tokens=50,
            completion_tokens=50,
            metadata={"provider": "ollama"},
        )
        assert response.tokens_used == 100
        assert response.metadata["provider"] == "ollama"


# === OllamaProvider Tests ===


class TestOllamaProvider:
    """Tests for OllamaProvider."""

    def test_provider_attributes(self):
        """Provider should have correct attributes."""
        provider = OllamaProvider()
        assert provider.provider_name == "ollama"
        assert provider.base_url == "http://localhost:11434"
        assert provider.model == "llama3.2"

    def test_custom_configuration(self):
        """Provider should accept custom config."""
        provider = OllamaProvider(
            base_url="http://gpu1:11434",
            model="llama3.3:70b",
            timeout=180,
        )
        assert provider.base_url == "http://gpu1:11434"
        assert provider.model == "llama3.3:70b"
        assert provider.timeout == 180

    @pytest.mark.asyncio
    async def test_complete_success(self):
        """Complete should return LLMResponse on success."""
        provider = OllamaProvider()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "Test response"},
            "eval_count": 50,
            "prompt_eval_count": 30,
        }
        mock_response.raise_for_status = MagicMock()

        with patch("services.llm.ollama.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            response = await provider.complete("Test prompt")

        assert response.text == "Test response"
        assert response.model == "llama3.2"
        assert response.tokens_used == 50

    @pytest.mark.asyncio
    async def test_is_available_success(self):
        """is_available should return True when API responds."""
        provider = OllamaProvider()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("services.llm.ollama.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await provider.is_available()

        assert result is True

    @pytest.mark.asyncio
    async def test_is_available_failure(self):
        """is_available should return False on connection error."""
        provider = OllamaProvider()

        with patch("services.llm.ollama.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=Exception("Connection refused")
            )

            result = await provider.is_available()

        assert result is False


# === OpenRouterProvider Tests ===


class TestOpenRouterProvider:
    """Tests for OpenRouterProvider."""

    def test_provider_attributes(self):
        """Provider should have correct attributes."""
        provider = OpenRouterProvider(api_key="test-key")
        assert provider.provider_name == "openrouter"
        assert provider.api_key == "test-key"
        assert provider.model == "mistralai/mistral-7b-instruct:free"

    def test_custom_configuration(self):
        """Provider should accept custom config."""
        provider = OpenRouterProvider(
            api_key="my-key",
            model="meta-llama/llama-3.3-70b-instruct",
            timeout=120,
        )
        assert provider.model == "meta-llama/llama-3.3-70b-instruct"
        assert provider.timeout == 120

    @pytest.mark.asyncio
    async def test_complete_success(self):
        """Complete should return LLMResponse on success."""
        provider = OpenRouterProvider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Test response"}, "finish_reason": "stop"}],
            "model": "mistralai/mistral-7b-instruct:free",
            "usage": {"total_tokens": 100, "prompt_tokens": 50, "completion_tokens": 50},
        }
        mock_response.raise_for_status = MagicMock()

        with patch("services.llm.openrouter.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            response = await provider.complete("Test prompt")

        assert response.text == "Test response"
        assert response.tokens_used == 100

    @pytest.mark.asyncio
    async def test_is_available_without_key(self):
        """is_available should return False without API key."""
        provider = OpenRouterProvider(api_key="")

        result = await provider.is_available()

        assert result is False


# === LLMService Tests ===


class TestLLMService:
    """Tests for LLMService."""

    def test_requires_at_least_one_provider(self):
        """Service should require at least one provider."""
        with pytest.raises(ValueError, match="At least one provider"):
            LLMService([])

    @pytest.mark.asyncio
    async def test_uses_first_provider(self):
        """Service should use first available provider."""
        provider1 = MagicMock(spec=BaseLLMProvider)
        provider1.provider_name = "test1"
        provider1.complete = AsyncMock(
            return_value=LLMResponse(text="Response 1", model="test")
        )

        provider2 = MagicMock(spec=BaseLLMProvider)
        provider2.provider_name = "test2"
        provider2.complete = AsyncMock(
            return_value=LLMResponse(text="Response 2", model="test")
        )

        service = LLMService([provider1, provider2])
        response = await service.complete("Test")

        assert response.text == "Response 1"
        provider1.complete.assert_called_once()
        provider2.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self):
        """Service should fall back to next provider on failure."""
        provider1 = MagicMock(spec=BaseLLMProvider)
        provider1.provider_name = "test1"
        provider1.complete = AsyncMock(side_effect=Exception("Provider 1 failed"))

        provider2 = MagicMock(spec=BaseLLMProvider)
        provider2.provider_name = "test2"
        provider2.complete = AsyncMock(
            return_value=LLMResponse(text="Fallback response", model="test")
        )

        service = LLMService([provider1, provider2])
        response = await service.complete("Test")

        assert response.text == "Fallback response"
        provider1.complete.assert_called_once()
        provider2.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_when_all_fail(self):
        """Service should raise when all providers fail."""
        provider1 = MagicMock(spec=BaseLLMProvider)
        provider1.provider_name = "test1"
        provider1.complete = AsyncMock(side_effect=Exception("Failed 1"))

        provider2 = MagicMock(spec=BaseLLMProvider)
        provider2.provider_name = "test2"
        provider2.complete = AsyncMock(side_effect=Exception("Failed 2"))

        service = LLMService([provider1, provider2])

        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            await service.complete("Test")

    @pytest.mark.asyncio
    async def test_check_availability(self):
        """check_availability should return status of all providers."""
        provider1 = MagicMock(spec=BaseLLMProvider)
        provider1.provider_name = "test1"
        provider1.is_available = AsyncMock(return_value=True)

        provider2 = MagicMock(spec=BaseLLMProvider)
        provider2.provider_name = "test2"
        provider2.is_available = AsyncMock(return_value=False)

        service = LLMService([provider1, provider2])
        result = await service.check_availability()

        assert result == {"test1": True, "test2": False}


# === ItemProcessor Tests ===


class TestItemProcessor:
    """Tests for ItemProcessor."""

    @pytest.fixture
    def mock_llm_service(self):
        """Create mock LLM service."""
        service = MagicMock(spec=LLMService)
        return service

    @pytest.fixture
    def processor(self, mock_llm_service):
        """Create processor with mock service."""
        from services.processor import ItemProcessor
        return ItemProcessor(mock_llm_service)

    @pytest.fixture
    def sample_item(self):
        """Create sample item for testing."""
        from models import Item
        item = Item(
            id=1,
            channel_id=1,
            external_id="test-123",
            title="Haushaltskürzungen bedrohen Pflegeeinrichtungen",
            content="Die geplanten Kürzungen im Landeshaushalt gefährden soziale Einrichtungen...",
            url="https://example.com/article",
        )
        return item

    def test_calculate_keyword_score_high(self, processor, sample_item):
        """High priority keywords should increase score significantly."""
        score, priority = processor.calculate_keyword_score(sample_item)

        # Should match "kürzungen" (high priority keyword)
        assert score > 50
        from models import Priority
        assert priority in [Priority.HIGH, Priority.MEDIUM]

    def test_calculate_keyword_score_no_match(self, processor):
        """Items without keywords should have base score."""
        from models import Item
        item = Item(
            id=1,
            channel_id=1,
            external_id="test-456",
            title="Wetter in Hessen",
            content="Es wird sonnig und warm.",
            url="https://example.com/weather",
        )

        score, priority = processor.calculate_keyword_score(item)

        assert score == 50  # Base score
        from models import Priority
        # Score of 50 falls into LOW priority range (>= 40)
        assert priority == Priority.LOW

    @pytest.mark.asyncio
    async def test_summarize(self, processor, sample_item, mock_llm_service):
        """Summarize should return LLM-generated summary."""
        mock_llm_service.complete = AsyncMock(
            return_value=LLMResponse(
                text="Die Haushaltskürzungen bedrohen Pflegeeinrichtungen in Hessen.",
                model="test",
            )
        )

        summary = await processor.summarize(sample_item)

        assert summary is not None
        assert "Haushaltskürzungen" in summary
        mock_llm_service.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_summarize_failure(self, processor, sample_item, mock_llm_service):
        """Summarize should return None on failure."""
        mock_llm_service.complete = AsyncMock(side_effect=Exception("LLM error"))

        summary = await processor.summarize(sample_item)

        assert summary is None
