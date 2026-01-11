"""Tests for classifier toggle functionality (CLASSIFIER_USE_PRIORITY, CLASSIFIER_USE_AK).

Note: Priority mapping (old → new):
- critical → HIGH
- high → MEDIUM
- medium → LOW
- low → NONE (not relevant)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, ConnectorType, Priority, Source
from services.pipeline import Pipeline, RawItem


class TestClassifierPriorityToggle:
    """Tests for CLASSIFIER_USE_PRIORITY toggle."""

    @pytest.mark.asyncio
    async def test_classifier_priority_disabled_uses_llm_priority(
        self, db_session: AsyncSession
    ):
        """When classifier_use_priority=False, LLM priority should be used."""
        source = Source(name="Test", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/feed.xml"},
            enabled=True,
        )
        db_session.add(channel)
        await db_session.flush()

        # Mock processor (LLM) - returns "high" which maps to MEDIUM
        mock_processor = MagicMock()
        mock_processor.analyze = AsyncMock(
            return_value={
                "summary": "LLM summary",
                "relevant": True,
                "priority": "high",  # Maps to Priority.MEDIUM
                "assigned_ak": "AK2",
            }
        )
        mock_processor.calculate_keyword_score = MagicMock(return_value=(50, []))

        # Mock relevance filter - returns "critical" from classifier
        mock_filter = MagicMock()
        mock_filter.should_process = AsyncMock(
            return_value=(
                True,
                {
                    "relevant": True,
                    "relevance_confidence": 0.9,
                    "priority": "critical",
                    "priority_confidence": 0.85,
                    "ak": "AK1",
                    "ak_confidence": 0.75,
                },
            )
        )
        mock_filter.index_items_batch = AsyncMock(return_value=1)

        with patch("services.pipeline.settings") as mock_settings:
            mock_settings.classifier_use_priority = False
            mock_settings.classifier_use_ak = False

            pipeline = Pipeline(
                db_session,
                processor=mock_processor,
                relevance_filter=mock_filter,
            )

            raw_items = [
                RawItem(
                    external_id="test-1",
                    title="Test Article",
                    content="Test content",
                    url="https://test.com/1",
                )
            ]

            items = await pipeline.process(raw_items, channel)

            assert len(items) == 1
            item = items[0]
            # LLM "high" maps to Priority.MEDIUM
            assert item.priority == Priority.MEDIUM

    @pytest.mark.asyncio
    async def test_classifier_priority_enabled_uses_classifier_priority(
        self, db_session: AsyncSession
    ):
        """When classifier_use_priority=True, classifier priority should override LLM."""
        source = Source(name="Test", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/feed.xml"},
            enabled=True,
        )
        db_session.add(channel)
        await db_session.flush()

        # Mock processor (LLM) - returns "low" which maps to NONE
        mock_processor = MagicMock()
        mock_processor.analyze = AsyncMock(
            return_value={
                "summary": "LLM summary",
                "relevant": True,
                "priority": "low",
                "assigned_ak": "AK2",
            }
        )
        mock_processor.calculate_keyword_score = MagicMock(return_value=(50, []))

        # Mock relevance filter - returns "critical" → HIGH
        mock_filter = MagicMock()
        mock_filter.should_process = AsyncMock(
            return_value=(
                True,
                {
                    "relevant": True,
                    "relevance_confidence": 0.9,
                    "priority": "critical",
                    "priority_confidence": 0.95,
                    "ak": "AK1",
                    "ak_confidence": 0.75,
                },
            )
        )
        mock_filter.index_items_batch = AsyncMock(return_value=1)

        with patch("services.pipeline.settings") as mock_settings:
            mock_settings.classifier_use_priority = True
            mock_settings.classifier_use_ak = False

            pipeline = Pipeline(
                db_session,
                processor=mock_processor,
                relevance_filter=mock_filter,
            )

            raw_items = [
                RawItem(
                    external_id="test-1",
                    title="Test Article",
                    content="Test content",
                    url="https://test.com/1",
                )
            ]

            items = await pipeline.process(raw_items, channel)

            assert len(items) == 1
            item = items[0]
            # Classifier "critical" → Priority.HIGH
            assert item.priority == Priority.HIGH
            assert item.priority_score == 90

    @pytest.mark.asyncio
    async def test_classifier_priority_all_levels(self, db_session: AsyncSession):
        """Test all classifier priority levels map correctly.

        Classifier output → new Priority:
        - "critical" → HIGH (score 90)
        - "high" → MEDIUM (score 70)
        - "medium" → LOW (score 50)
        - "low" → NONE (score 30)
        """
        source = Source(name="Test", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/feed.xml"},
            enabled=True,
        )
        db_session.add(channel)
        await db_session.flush()

        priority_tests = [
            ("critical", Priority.HIGH, 90),
            ("high", Priority.MEDIUM, 70),
            ("medium", Priority.LOW, 50),
            ("low", Priority.NONE, 30),
        ]

        for clf_priority, expected_priority, expected_score in priority_tests:
            await db_session.rollback()
            db_session.add(source)
            db_session.add(channel)
            await db_session.flush()

            mock_processor = MagicMock()
            mock_processor.analyze = AsyncMock(
                return_value={
                    "summary": "LLM summary",
                    "relevant": True,
                    "priority": "medium",
                }
            )
            mock_processor.calculate_keyword_score = MagicMock(return_value=(50, []))

            mock_filter = MagicMock()
            mock_filter.should_process = AsyncMock(
                return_value=(
                    True,
                    {
                        "relevant": True,
                        "relevance_confidence": 0.9,
                        "priority": clf_priority,
                        "priority_confidence": 0.8,
                    },
                )
            )
            mock_filter.index_items_batch = AsyncMock(return_value=1)

            with patch("services.pipeline.settings") as mock_settings:
                mock_settings.classifier_use_priority = True
                mock_settings.classifier_use_ak = False

                pipeline = Pipeline(
                    db_session,
                    processor=mock_processor,
                    relevance_filter=mock_filter,
                )

                raw_items = [
                    RawItem(
                        external_id=f"test-{clf_priority}",
                        title="Test Article",
                        content="Test content",
                        url=f"https://test.com/{clf_priority}",
                    )
                ]

                items = await pipeline.process(raw_items, channel)

                assert len(items) == 1
                item = items[0]
                assert item.priority == expected_priority, \
                    f"Expected {expected_priority} for '{clf_priority}'"
                assert item.priority_score == expected_score, \
                    f"Expected score {expected_score} for '{clf_priority}'"


class TestClassifierAKToggle:
    """Tests for CLASSIFIER_USE_AK toggle."""

    @pytest.mark.asyncio
    async def test_classifier_ak_disabled_uses_llm_ak(self, db_session: AsyncSession):
        """When classifier_use_ak=False, LLM AK should be used."""
        source = Source(name="Test", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/feed.xml"},
            enabled=True,
        )
        db_session.add(channel)
        await db_session.flush()

        mock_processor = MagicMock()
        mock_processor.analyze = AsyncMock(
            return_value={
                "summary": "LLM summary",
                "relevant": True,
                "priority": "medium",
                "assigned_ak": "AK2",
            }
        )
        mock_processor.calculate_keyword_score = MagicMock(return_value=(50, []))

        mock_filter = MagicMock()
        mock_filter.should_process = AsyncMock(
            return_value=(
                True,
                {
                    "relevant": True,
                    "relevance_confidence": 0.9,
                    "priority": "medium",
                    "ak": "AK1",
                    "ak_confidence": 0.85,
                },
            )
        )
        mock_filter.index_items_batch = AsyncMock(return_value=1)

        with patch("services.pipeline.settings") as mock_settings:
            mock_settings.classifier_use_priority = False
            mock_settings.classifier_use_ak = False

            pipeline = Pipeline(
                db_session,
                processor=mock_processor,
                relevance_filter=mock_filter,
            )

            raw_items = [
                RawItem(
                    external_id="test-1",
                    title="Test Article",
                    content="Test content",
                    url="https://test.com/1",
                )
            ]

            items = await pipeline.process(raw_items, channel)

            assert len(items) == 1
            item = items[0]
            # Should use LLM AK (AK2), not classifier (AK1)
            assert item.metadata_.get("llm_analysis", {}).get("assigned_ak") == "AK2"

    @pytest.mark.asyncio
    async def test_classifier_ak_enabled_uses_classifier_ak(
        self, db_session: AsyncSession
    ):
        """When classifier_use_ak=True, classifier AK should override LLM."""
        source = Source(name="Test", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/feed.xml"},
            enabled=True,
        )
        db_session.add(channel)
        await db_session.flush()

        mock_processor = MagicMock()
        mock_processor.analyze = AsyncMock(
            return_value={
                "summary": "LLM summary",
                "relevant": True,
                "priority": "medium",
                "assigned_ak": "AK5",
            }
        )
        mock_processor.calculate_keyword_score = MagicMock(return_value=(50, []))

        mock_filter = MagicMock()
        mock_filter.should_process = AsyncMock(
            return_value=(
                True,
                {
                    "relevant": True,
                    "relevance_confidence": 0.9,
                    "priority": "medium",
                    "ak": "QAG",
                    "ak_confidence": 0.92,
                },
            )
        )
        mock_filter.index_items_batch = AsyncMock(return_value=1)

        with patch("services.pipeline.settings") as mock_settings:
            mock_settings.classifier_use_priority = False
            mock_settings.classifier_use_ak = True

            pipeline = Pipeline(
                db_session,
                processor=mock_processor,
                relevance_filter=mock_filter,
            )

            raw_items = [
                RawItem(
                    external_id="test-1",
                    title="Test Article",
                    content="Test content",
                    url="https://test.com/1",
                )
            ]

            items = await pipeline.process(raw_items, channel)

            assert len(items) == 1
            item = items[0]
            # Should use classifier AK (QAG)
            assert item.metadata_.get("llm_analysis", {}).get("assigned_ak") == "QAG"
            assert item.metadata_.get("llm_analysis", {}).get("ak_source") == "classifier"


class TestBothTogglesEnabled:
    """Tests for both toggles enabled simultaneously."""

    @pytest.mark.asyncio
    async def test_both_toggles_enabled(self, db_session: AsyncSession):
        """When both toggles enabled, both classifier values should be used."""
        source = Source(name="Test", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/feed.xml"},
            enabled=True,
        )
        db_session.add(channel)
        await db_session.flush()

        mock_processor = MagicMock()
        mock_processor.analyze = AsyncMock(
            return_value={
                "summary": "LLM summary",
                "relevant": True,
                "priority": "low",
                "assigned_ak": "AK5",
            }
        )
        mock_processor.calculate_keyword_score = MagicMock(return_value=(50, []))

        mock_filter = MagicMock()
        mock_filter.should_process = AsyncMock(
            return_value=(
                True,
                {
                    "relevant": True,
                    "relevance_confidence": 0.95,
                    "priority": "critical",
                    "priority_confidence": 0.9,
                    "ak": "AK2",
                    "ak_confidence": 0.88,
                },
            )
        )
        mock_filter.index_items_batch = AsyncMock(return_value=1)

        with patch("services.pipeline.settings") as mock_settings:
            mock_settings.classifier_use_priority = True
            mock_settings.classifier_use_ak = True

            pipeline = Pipeline(
                db_session,
                processor=mock_processor,
                relevance_filter=mock_filter,
            )

            raw_items = [
                RawItem(
                    external_id="test-1",
                    title="Test Article",
                    content="Test content",
                    url="https://test.com/1",
                )
            ]

            items = await pipeline.process(raw_items, channel)

            assert len(items) == 1
            item = items[0]
            # Priority: classifier "critical" → HIGH
            assert item.priority == Priority.HIGH
            assert item.priority_score == 90
            # AK: classifier AK2
            assert item.metadata_["llm_analysis"]["assigned_ak"] == "AK2"
            assert item.metadata_["llm_analysis"]["ak_source"] == "classifier"


class TestNoPreFilterResult:
    """Tests when pre-filter result is not available."""

    @pytest.mark.asyncio
    async def test_toggle_has_no_effect_without_prefilter(
        self, db_session: AsyncSession
    ):
        """When no pre-filter result, toggles should have no effect."""
        source = Source(name="Test", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/feed.xml"},
            enabled=True,
        )
        db_session.add(channel)
        await db_session.flush()

        # LLM "high" → MEDIUM
        mock_processor = MagicMock()
        mock_processor.analyze = AsyncMock(
            return_value={
                "summary": "LLM summary",
                "relevant": True,
                "priority": "high",
                "assigned_ak": "AK3",
            }
        )
        mock_processor.calculate_keyword_score = MagicMock(return_value=(50, []))

        mock_filter = MagicMock()
        mock_filter.should_process = AsyncMock(return_value=(True, None))
        mock_filter.index_items_batch = AsyncMock(return_value=1)

        with patch("services.pipeline.settings") as mock_settings:
            mock_settings.classifier_use_priority = True
            mock_settings.classifier_use_ak = True

            pipeline = Pipeline(
                db_session,
                processor=mock_processor,
                relevance_filter=mock_filter,
            )

            raw_items = [
                RawItem(
                    external_id="test-1",
                    title="Test Article",
                    content="Test content",
                    url="https://test.com/1",
                )
            ]

            items = await pipeline.process(raw_items, channel)

            assert len(items) == 1
            item = items[0]
            # LLM "high" → MEDIUM
            assert item.priority == Priority.MEDIUM
            assert item.metadata_["llm_analysis"]["assigned_ak"] == "AK3"

    @pytest.mark.asyncio
    async def test_toggle_has_no_effect_without_filter(self, db_session: AsyncSession):
        """When no relevance filter configured, toggles should have no effect."""
        source = Source(name="Test", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Test Channel",
            connector_type=ConnectorType.RSS,
            config={"url": "https://test.com/feed.xml"},
            enabled=True,
        )
        db_session.add(channel)
        await db_session.flush()

        # LLM "medium" → LOW
        mock_processor = MagicMock()
        mock_processor.analyze = AsyncMock(
            return_value={
                "summary": "LLM summary",
                "relevant": True,
                "priority": "medium",
                "assigned_ak": "AK4",
            }
        )
        mock_processor.calculate_keyword_score = MagicMock(return_value=(50, []))

        with patch("services.pipeline.settings") as mock_settings:
            mock_settings.classifier_use_priority = True
            mock_settings.classifier_use_ak = True

            pipeline = Pipeline(
                db_session,
                processor=mock_processor,
                relevance_filter=None,
            )

            raw_items = [
                RawItem(
                    external_id="test-1",
                    title="Test Article",
                    content="Test content",
                    url="https://test.com/1",
                )
            ]

            items = await pipeline.process(raw_items, channel)

            assert len(items) == 1
            item = items[0]
            # LLM "medium" → LOW
            assert item.priority == Priority.LOW
            assert item.metadata_["llm_analysis"]["assigned_ak"] == "AK4"
