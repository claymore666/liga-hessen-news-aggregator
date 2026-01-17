"""Tests for email briefing service."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from models import Priority
from services.email import (
    EmailConfig,
    BriefingEmail,
    _get_priority_value,
    _priority_to_rank,
    send_daily_briefing,
)


@pytest.fixture
def email_config():
    """Create EmailConfig for testing."""
    return EmailConfig(
        recipients=["test@example.com"],
        subject_prefix="[Test]",
        include_summary=True,
        include_content=False,
        min_priority=Priority.NONE,
    )


@pytest.fixture
def briefing(email_config):
    """Create BriefingEmail instance for testing."""
    return BriefingEmail(email_config)


@pytest.fixture
def mock_item():
    """Create a mock Item for testing."""
    item = MagicMock()
    item.title = "Test Article Title"
    item.summary = "This is the article summary."
    item.url = "https://example.com/article"
    item.priority = Priority.MEDIUM
    item.source = MagicMock()
    item.source.name = "Test Source"
    return item


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_priority_value_enum(self):
        """Should extract value from enum."""
        assert _get_priority_value(Priority.HIGH) == "high"
        assert _get_priority_value(Priority.MEDIUM) == "medium"
        assert _get_priority_value(Priority.LOW) == "low"
        assert _get_priority_value(Priority.NONE) == "none"

    def test_get_priority_value_string(self):
        """Should handle string input."""
        assert _get_priority_value("high") == "high"
        assert _get_priority_value("medium") == "medium"

    def test_get_priority_value_none(self):
        """Should handle None input."""
        assert _get_priority_value(None) == "none"

    def test_priority_to_rank(self):
        """Should convert priority to numeric rank."""
        assert _priority_to_rank(Priority.HIGH) == 3
        assert _priority_to_rank(Priority.MEDIUM) == 2
        assert _priority_to_rank(Priority.LOW) == 1
        assert _priority_to_rank(Priority.NONE) == 0

    def test_priority_to_rank_unknown(self):
        """Should return 0 for unknown priority."""
        assert _priority_to_rank("unknown") == 0


class TestEmailConfig:
    """Tests for EmailConfig model."""

    def test_default_values(self):
        """Should have sensible defaults."""
        config = EmailConfig(recipients=["test@example.com"])
        assert config.subject_prefix == "[Liga News]"
        assert config.include_summary is True
        assert config.include_content is False
        assert config.min_priority == Priority.NONE

    def test_custom_values(self, email_config):
        """Should accept custom values."""
        assert email_config.subject_prefix == "[Test]"
        assert len(email_config.recipients) == 1


class TestBriefingEmailGrouping:
    """Tests for item grouping by priority."""

    def test_group_by_priority_single(self, briefing, mock_item):
        """Should group single item correctly."""
        grouped = briefing._group_by_priority([mock_item])
        assert len(grouped[Priority.MEDIUM]) == 1
        assert len(grouped[Priority.HIGH]) == 0

    def test_group_by_priority_multiple(self, briefing):
        """Should group multiple items correctly."""
        items = []
        for priority in [Priority.HIGH, Priority.HIGH, Priority.LOW]:
            item = MagicMock()
            item.priority = priority
            items.append(item)

        grouped = briefing._group_by_priority(items)
        assert len(grouped[Priority.HIGH]) == 2
        assert len(grouped[Priority.LOW]) == 1
        assert len(grouped[Priority.MEDIUM]) == 0

    def test_group_respects_min_priority(self, email_config):
        """Should filter items below min_priority."""
        email_config.min_priority = Priority.MEDIUM
        briefing = BriefingEmail(email_config)

        items = []
        for priority in [Priority.HIGH, Priority.LOW, Priority.NONE]:
            item = MagicMock()
            item.priority = priority
            items.append(item)

        grouped = briefing._group_by_priority(items)
        assert len(grouped[Priority.HIGH]) == 1
        assert len(grouped[Priority.LOW]) == 0  # Filtered
        assert len(grouped[Priority.NONE]) == 0  # Filtered


class TestFormatItem:
    """Tests for item formatting."""

    def test_format_item_text_basic(self, briefing, mock_item):
        """Should format item for plain text."""
        text = briefing._format_item_text(mock_item)
        assert "Test Article Title" in text
        assert "Test Source" in text
        assert "https://example.com/article" in text

    def test_format_item_text_with_summary(self, briefing, mock_item):
        """Should include summary when configured."""
        text = briefing._format_item_text(mock_item)
        assert "This is the article summary" in text

    def test_format_item_text_without_summary(self, email_config, mock_item):
        """Should exclude summary when configured."""
        email_config.include_summary = False
        briefing = BriefingEmail(email_config)
        text = briefing._format_item_text(mock_item)
        assert "article summary" not in text

    def test_format_item_html_basic(self, briefing, mock_item):
        """Should format item for HTML."""
        html = briefing._format_item_html(mock_item)
        assert "<li" in html
        assert "Test Article Title" in html
        assert 'href="https://example.com/article"' in html

    def test_format_item_html_with_source(self, briefing, mock_item):
        """Should include source in HTML."""
        html = briefing._format_item_html(mock_item)
        assert "Test Source" in html


class TestGenerateTextBody:
    """Tests for text body generation."""

    def test_generate_text_body_empty(self, briefing):
        """Should handle empty items list."""
        date = datetime(2024, 1, 15)
        text = briefing.generate_text_body([], date)
        assert "Keine neuen Meldungen" in text

    def test_generate_text_body_with_items(self, briefing, mock_item):
        """Should format items in text body."""
        date = datetime(2024, 1, 15)
        text = briefing.generate_text_body([mock_item], date)
        assert "15.01.2024" in text
        assert "Test Article Title" in text
        assert "Zusammenfassung" in text

    def test_generate_text_body_summary_counts(self, briefing):
        """Should include summary counts."""
        items = []
        for priority in [Priority.HIGH, Priority.HIGH, Priority.LOW]:
            item = MagicMock()
            item.priority = priority
            item.title = "Test"
            item.source = MagicMock()
            item.source.name = "Source"
            item.summary = None
            item.url = "https://test.com"
            items.append(item)

        date = datetime(2024, 1, 15)
        text = briefing.generate_text_body(items, date)
        assert "WICHTIG: 2" in text
        assert "NIEDRIG: 1" in text


class TestGenerateHtmlBody:
    """Tests for HTML body generation."""

    def test_generate_html_body_empty(self, briefing):
        """Should handle empty items list."""
        date = datetime(2024, 1, 15)
        html = briefing.generate_html_body([], date)
        assert "Keine neuen Meldungen" in html

    def test_generate_html_body_structure(self, briefing, mock_item):
        """Should have proper HTML structure."""
        date = datetime(2024, 1, 15)
        html = briefing.generate_html_body([mock_item], date)
        assert "<!DOCTYPE html>" in html
        assert "<html>" in html
        assert "</html>" in html
        assert "Daily Briefing" in html

    def test_generate_html_body_with_items(self, briefing, mock_item):
        """Should include items in HTML."""
        date = datetime(2024, 1, 15)
        html = briefing.generate_html_body([mock_item], date)
        assert "Test Article Title" in html
        assert "Test Source" in html


class TestSendEmail:
    """Tests for email sending."""

    def test_send_no_recipients(self, email_config):
        """Should fail when no recipients configured."""
        email_config.recipients = []
        briefing = BriefingEmail(email_config)
        success, message = briefing.send([])
        assert success is False
        assert "Empfänger" in message

    def test_send_success(self, briefing, mock_item):
        """Should send email via sendmail."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            success, message = briefing.send([mock_item])

        assert success is True
        assert "gesendet" in message
        mock_run.assert_called_once()

        # Check sendmail was called correctly
        call_args = mock_run.call_args
        assert call_args[0][0] == ["/usr/sbin/sendmail", "-t"]

    def test_send_failure(self, briefing, mock_item):
        """Should handle sendmail failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="Error")
            success, message = briefing.send([mock_item])

        assert success is False
        assert "Fehler" in message

    def test_send_timeout(self, briefing, mock_item):
        """Should handle sendmail timeout."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("sendmail", 30)
            success, message = briefing.send([mock_item])

        assert success is False
        assert "Timeout" in message

    def test_send_not_found(self, briefing, mock_item):
        """Should handle missing sendmail."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            success, message = briefing.send([mock_item])

        assert success is False
        assert "nicht gefunden" in message


class TestSendDailyBriefing:
    """Tests for convenience function."""

    @pytest.mark.asyncio
    async def test_send_daily_briefing(self, mock_item):
        """Should send briefing with default config."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            success, message = await send_daily_briefing(
                items=[mock_item],
                recipients=["test@example.com"],
                min_priority=Priority.LOW,
            )

        assert success is True


class TestPriorityLabels:
    """Tests for priority labels and order."""

    def test_all_priorities_have_labels(self, briefing):
        """All priorities should have labels."""
        for priority in [Priority.HIGH, Priority.MEDIUM, Priority.LOW, Priority.NONE]:
            assert priority in briefing.PRIORITY_LABELS
            assert len(briefing.PRIORITY_LABELS[priority]) > 0

    def test_priority_order(self, briefing):
        """Priority order should be high to low."""
        order = briefing.PRIORITY_ORDER
        assert order[0] == Priority.HIGH
        assert order[-1] == Priority.NONE
