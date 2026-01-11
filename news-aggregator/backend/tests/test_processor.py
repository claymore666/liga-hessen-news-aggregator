"""Tests for processor parsing and priority logic."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from services.processor import ItemProcessor
from services.llm import LLMResponse


@pytest.fixture
def processor():
    """Create processor instance for testing."""
    mock_llm = MagicMock()
    return ItemProcessor(llm_service=mock_llm)


class TestParseAnalysisResponse:
    """Tests for _parse_analysis_response method."""

    def test_parse_valid_json(self, processor):
        """Valid JSON should be parsed correctly."""
        response = LLMResponse(
            text='{"summary": "Test", "relevant": true, "priority": "high"}',
            model="test",
            provider="test",
        )
        result = processor._parse_analysis_response(response)
        assert result["summary"] == "Test"
        assert result["relevant"] is True
        assert result["priority"] == "high"

    def test_parse_json_with_markdown_code_block(self, processor):
        """JSON wrapped in markdown code blocks should be parsed."""
        response = LLMResponse(
            text='```json\n{"summary": "Test", "relevant": false, "priority": "low"}\n```',
            model="test",
            provider="test",
        )
        result = processor._parse_analysis_response(response)
        assert result["summary"] == "Test"
        assert result["relevant"] is False
        assert result["priority"] == "low"

    def test_parse_json_with_triple_backticks_no_language(self, processor):
        """JSON wrapped in plain triple backticks should be parsed."""
        response = LLMResponse(
            text='```\n{"summary": "Test", "priority": "medium"}\n```',
            model="test",
            provider="test",
        )
        result = processor._parse_analysis_response(response)
        assert result["summary"] == "Test"
        assert result["priority"] == "medium"

    def test_parse_json_with_surrounding_text(self, processor):
        """JSON embedded in other text should be extracted."""
        response = LLMResponse(
            text='Here is the analysis:\n{"summary": "Test", "priority": "medium"}\nEnd.',
            model="test",
            provider="test",
        )
        result = processor._parse_analysis_response(response)
        assert result["summary"] == "Test"
        assert result["priority"] == "medium"

    def test_parse_nested_json(self, processor):
        """Nested JSON objects should be handled correctly."""
        response = LLMResponse(
            text='{"summary": "Test", "tags": ["a", "b"], "nested": {"key": "value"}}',
            model="test",
            provider="test",
        )
        result = processor._parse_analysis_response(response)
        assert result["summary"] == "Test"
        assert result["tags"] == ["a", "b"]
        assert result["nested"]["key"] == "value"

    def test_parse_json_with_german_umlauts(self, processor):
        """JSON with German umlauts should be parsed correctly."""
        response = LLMResponse(
            text='{"summary": "Änderung der Kürzungen", "priority": "critical"}',
            model="test",
            provider="test",
        )
        result = processor._parse_analysis_response(response)
        assert result["summary"] == "Änderung der Kürzungen"

    def test_parse_json_with_newlines_in_summary(self, processor):
        """JSON with newlines in string values should be parsed."""
        response = LLMResponse(
            text='{"summary": "Line 1.\\nLine 2.", "priority": "low"}',
            model="test",
            provider="test",
        )
        result = processor._parse_analysis_response(response)
        assert "Line 1" in result["summary"]

    def test_parse_invalid_json_returns_default(self, processor):
        """Invalid JSON should return default analysis with low priority."""
        response = LLMResponse(
            text="RELEVANT: Nein\nBEGRÜNDUNG: Not relevant",
            model="test",
            provider="test",
        )
        result = processor._parse_analysis_response(response)
        # Should use default which has low priority
        assert result["priority"] == "low"
        assert result["relevant"] is False
        assert result["relevance_score"] == 0.0
        # Summary should contain the raw text
        assert "RELEVANT: Nein" in result["summary"]

    def test_parse_empty_response_returns_default(self, processor):
        """Empty response should return default analysis."""
        response = LLMResponse(text="", model="test", provider="test")
        result = processor._parse_analysis_response(response)
        assert result["priority"] == "low"
        assert result["relevant"] is False

    def test_parse_whitespace_only_returns_default(self, processor):
        """Whitespace-only response should return default analysis."""
        response = LLMResponse(text="   \n\t  ", model="test", provider="test")
        result = processor._parse_analysis_response(response)
        assert result["priority"] == "low"

    def test_parse_partial_json_returns_default(self, processor):
        """Incomplete JSON should return default analysis."""
        response = LLMResponse(
            text='{"summary": "Test", "priority":',
            model="test",
            provider="test",
        )
        result = processor._parse_analysis_response(response)
        assert result["priority"] == "low"

    def test_parse_json_array_returns_default(self, processor):
        """JSON array (not object) should return default analysis."""
        response = LLMResponse(
            text='["item1", "item2"]',
            model="test",
            provider="test",
        )
        result = processor._parse_analysis_response(response)
        # Should fall back to default since we expect object not array
        assert result["priority"] == "low"


class TestDefaultAnalysis:
    """Tests for _default_analysis method."""

    def test_default_analysis_has_low_priority(self, processor):
        """Default analysis should have low priority, not medium."""
        result = processor._default_analysis()
        assert result["priority"] == "low"
        assert result["relevant"] is False
        assert result["relevance_score"] == 0.0

    def test_default_analysis_preserves_summary(self, processor):
        """Default analysis should preserve provided summary text."""
        result = processor._default_analysis("Some fallback text")
        assert result["summary"] == "Some fallback text"
        assert result["priority"] == "low"

    def test_default_analysis_has_empty_tags(self, processor):
        """Default analysis should have empty tags list."""
        result = processor._default_analysis()
        assert result["tags"] == []

    def test_default_analysis_has_null_ak(self, processor):
        """Default analysis should have None for assigned_ak."""
        result = processor._default_analysis()
        assert result["assigned_ak"] is None


class TestPriorityLogic:
    """Tests for priority determination logic (simulates pipeline behavior)."""

    @staticmethod
    def apply_priority_logic(analysis: dict) -> str:
        """Simulate pipeline priority logic."""
        llm_priority = analysis.get("priority") or analysis.get("priority_suggestion")
        if analysis.get("relevant") is False:
            llm_priority = "low"
        return llm_priority

    def test_relevant_false_forces_low_priority(self):
        """When relevant=false, priority should be forced to low."""
        analysis = {
            "relevant": False,
            "priority": "critical",  # Even if LLM says critical
            "relevance_score": 0.2,
        }
        assert self.apply_priority_logic(analysis) == "low"

    def test_relevant_false_with_high_priority(self):
        """High priority should become low when relevant=false."""
        analysis = {"relevant": False, "priority": "high"}
        assert self.apply_priority_logic(analysis) == "low"

    def test_relevant_false_with_medium_priority(self):
        """Medium priority should become low when relevant=false."""
        analysis = {"relevant": False, "priority": "medium"}
        assert self.apply_priority_logic(analysis) == "low"

    def test_relevant_true_keeps_critical(self):
        """When relevant=true, critical priority should be kept."""
        analysis = {"relevant": True, "priority": "critical"}
        assert self.apply_priority_logic(analysis) == "critical"

    def test_relevant_true_keeps_high(self):
        """When relevant=true, high priority should be kept."""
        analysis = {"relevant": True, "priority": "high"}
        assert self.apply_priority_logic(analysis) == "high"

    def test_relevant_true_keeps_medium(self):
        """When relevant=true, medium priority should be kept."""
        analysis = {"relevant": True, "priority": "medium"}
        assert self.apply_priority_logic(analysis) == "medium"

    def test_relevant_true_keeps_low(self):
        """When relevant=true, low priority should be kept."""
        analysis = {"relevant": True, "priority": "low"}
        assert self.apply_priority_logic(analysis) == "low"

    def test_missing_relevant_field_keeps_priority(self):
        """When relevant field is missing, priority should be kept."""
        analysis = {"priority": "medium", "relevance_score": 0.5}
        assert self.apply_priority_logic(analysis) == "medium"

    def test_relevant_none_keeps_priority(self):
        """When relevant is None, priority should be kept."""
        analysis = {"relevant": None, "priority": "high"}
        assert self.apply_priority_logic(analysis) == "high"

    def test_priority_suggestion_fallback(self):
        """Should use priority_suggestion if priority is missing."""
        analysis = {"priority_suggestion": "high", "relevant": True}
        assert self.apply_priority_logic(analysis) == "high"

    def test_priority_takes_precedence_over_suggestion(self):
        """priority should take precedence over priority_suggestion."""
        analysis = {
            "priority": "critical",
            "priority_suggestion": "low",
            "relevant": True,
        }
        assert self.apply_priority_logic(analysis) == "critical"


class TestPriorityMapping:
    """Tests for LLM priority string to stored priority enum mapping.

    LLM outputs: critical, high, medium, low
    Stored priorities: high, medium, low, none

    Mapping: critical→high, high→medium, medium→low, low→none
    """

    @staticmethod
    def map_llm_priority(llm_priority: str | None) -> "Priority":
        """Map LLM output priority to stored priority enum."""
        from models import Priority

        if llm_priority == "critical":
            return Priority.HIGH
        elif llm_priority == "high":
            return Priority.MEDIUM
        elif llm_priority == "medium":
            return Priority.LOW
        else:
            return Priority.NONE

    def test_all_priority_values_map_correctly(self):
        """Test all LLM priority values map to correct stored enums."""
        from models import Priority

        # LLM output → Stored priority
        test_cases = [
            ("critical", Priority.HIGH),  # critical → high
            ("high", Priority.MEDIUM),    # high → medium
            ("medium", Priority.LOW),     # medium → low
            ("low", Priority.NONE),       # low → none
        ]

        for llm_priority, expected in test_cases:
            result = self.map_llm_priority(llm_priority)
            assert result == expected, f"Failed for {llm_priority}: got {result}, expected {expected}"

    def test_null_priority_maps_to_none(self):
        """None priority should map to NONE."""
        from models import Priority

        result = self.map_llm_priority(None)
        assert result == Priority.NONE

    def test_unknown_priority_maps_to_none(self):
        """Unknown priority string should map to NONE."""
        from models import Priority

        result = self.map_llm_priority("unknown")
        assert result == Priority.NONE


class TestAnalyzeMethod:
    """Tests for the analyze method."""

    @pytest.mark.asyncio
    async def test_analyze_formats_input_correctly(self, processor):
        """Analyze should format input in expected format."""
        from models import Item, Source
        from datetime import datetime

        # Mock the source
        source = MagicMock()
        source.name = "Test Source"

        # Create mock item
        item = MagicMock()
        item.title = "Test Title"
        item.content = "Test content for analysis"
        item.source = source
        item.published_at = datetime(2025, 1, 1)

        # Mock LLM response
        processor.llm.complete = AsyncMock(
            return_value=LLMResponse(
                text='{"summary": "Test", "relevant": true, "priority": "high"}',
                model="test",
                provider="test",
            )
        )

        result = await processor.analyze(item)

        # Check LLM was called
        processor.llm.complete.assert_called_once()

        # Check prompt format
        call_args = processor.llm.complete.call_args
        prompt = call_args[0][0]
        assert "Titel: Test Title" in prompt
        assert "Inhalt: Test content" in prompt
        assert "Quelle: Test Source" in prompt
        assert "Datum: 2025-01-01" in prompt

    @pytest.mark.asyncio
    async def test_analyze_handles_missing_source(self, processor):
        """Analyze should handle items without source."""
        item = MagicMock()
        item.title = "Test"
        item.content = "Content"
        item.source = None
        item.published_at = None

        processor.llm.complete = AsyncMock(
            return_value=LLMResponse(
                text='{"summary": "Test", "priority": "low"}',
                model="test",
                provider="test",
            )
        )

        result = await processor.analyze(item)

        call_args = processor.llm.complete.call_args
        prompt = call_args[0][0]
        assert "Quelle: Unbekannt" in prompt
        assert "Datum: Unbekannt" in prompt

    @pytest.mark.asyncio
    async def test_analyze_truncates_long_content(self, processor):
        """Analyze should truncate content longer than 2000 chars."""
        item = MagicMock()
        item.title = "Test"
        item.content = "A" * 5000  # Long content
        item.source = None
        item.published_at = None

        processor.llm.complete = AsyncMock(
            return_value=LLMResponse(
                text='{"summary": "Test", "priority": "low"}',
                model="test",
                provider="test",
            )
        )

        await processor.analyze(item)

        call_args = processor.llm.complete.call_args
        prompt = call_args[0][0]
        # Content should be truncated to 2000 chars
        assert len(prompt) < 5000

    @pytest.mark.asyncio
    async def test_analyze_returns_default_on_llm_error(self, processor):
        """Analyze should return default analysis when LLM fails."""
        item = MagicMock()
        item.title = "Test"
        item.content = "Content"
        item.source = None
        item.published_at = None

        processor.llm.complete = AsyncMock(side_effect=Exception("LLM Error"))

        result = await processor.analyze(item)

        assert result["priority"] == "low"
        assert result["relevant"] is False
