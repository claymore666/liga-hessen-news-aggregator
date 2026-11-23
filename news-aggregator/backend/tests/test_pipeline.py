"""Tests for the item processing pipeline."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, ConnectorType, Item, Priority, Rule, RuleType, Source
from services.pipeline import Pipeline, RawItem
from database import utcnow


class TestRawItem:
    """Tests for RawItem dataclass."""

    def test_create_raw_item(self):
        """Test creating a RawItem."""
        item = RawItem(
            external_id="123",
            title="Test Title",
            content="Test content",
            url="https://example.com",
        )

        assert item.external_id == "123"
        assert item.title == "Test Title"
        assert item.author is None
        assert item.published_at is not None

    def test_raw_item_with_metadata(self):
        """Test RawItem with metadata."""
        item = RawItem(
            external_id="123",
            title="Test",
            content="Content",
            url="https://example.com",
            metadata={"source": "twitter", "likes": 100},
        )

        assert item.metadata["source"] == "twitter"
        assert item.metadata["likes"] == 100


class TestPipeline:
    """Tests for Pipeline class."""

    @pytest.mark.asyncio
    async def test_normalize_content_strips_html(self, db_session: AsyncSession):
        """Test that HTML is stripped from content."""
        pipeline = Pipeline(db_session)

        raw = RawItem(
            external_id="1",
            title="<b>Bold Title</b>",
            content="<p>Paragraph with <a href='#'>link</a></p>",
            url="https://example.com",
        )

        normalized = pipeline._normalize_content(raw)

        assert "<" not in normalized.title
        assert "<" not in normalized.content
        assert "Bold Title" in normalized.title
        assert "Paragraph with link" in normalized.content

    @pytest.mark.asyncio
    async def test_normalize_content_fixes_entities(self, db_session: AsyncSession):
        """Test that HTML entities are decoded."""
        pipeline = Pipeline(db_session)

        raw = RawItem(
            external_id="1",
            title="Test &amp; Title",
            content="Content with &lt;tags&gt; and &quot;quotes&quot;",
            url="https://example.com",
        )

        normalized = pipeline._normalize_content(raw)

        assert "&" in normalized.title
        assert "<tags>" in normalized.content
        assert '"quotes"' in normalized.content

    @pytest.mark.asyncio
    async def test_normalize_content_normalizes_whitespace(
        self, db_session: AsyncSession
    ):
        """Test that whitespace is normalized."""
        pipeline = Pipeline(db_session)

        raw = RawItem(
            external_id="1",
            title="Title   with   spaces",
            content="Content\n\n\nwith\t\tnewlines",
            url="https://example.com",
        )

        normalized = pipeline._normalize_content(raw)

        assert "  " not in normalized.title
        assert "\n" not in normalized.content
        assert "\t" not in normalized.content

    @pytest.mark.asyncio
    async def test_compute_hash(self, db_session: AsyncSession):
        """Test content hash computation."""
        pipeline = Pipeline(db_session)

        hash1 = pipeline._compute_hash("Hello World")
        hash2 = pipeline._compute_hash("Hello World")
        hash3 = pipeline._compute_hash("Different content")

        assert hash1 == hash2
        assert hash1 != hash3
        assert len(hash1) == 64  # SHA-256 hex

    @pytest.mark.asyncio
    async def test_is_duplicate_by_external_id(self, db_session: AsyncSession):
        """Test duplicate detection by external_id."""
        source = Source(name="Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)
        await db_session.flush()

        # Create existing item
        existing = Item(
            channel_id=channel.id,
            external_id="existing-123",
            title="Existing",
            content="Content",
            url="https://example.com",
            published_at=utcnow(),
            content_hash="somehash",
        )
        db_session.add(existing)
        await db_session.flush()

        pipeline = Pipeline(db_session)

        # Same external_id should be duplicate
        is_dup = await pipeline._is_duplicate(channel.id, "existing-123", "differenthash")
        assert is_dup is True

        # Different external_id should not be duplicate
        is_dup = await pipeline._is_duplicate(channel.id, "new-456", "differenthash")
        assert is_dup is False

    @pytest.mark.asyncio
    async def test_is_duplicate_by_content_hash(self, db_session: AsyncSession):
        """Test duplicate detection by content hash."""
        source = Source(name="Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)
        await db_session.flush()

        content_hash = "abc123def456"
        existing = Item(
            channel_id=channel.id,
            external_id="original",
            title="Original",
            content="Same content",
            url="https://example.com",
            published_at=utcnow(),
            content_hash=content_hash,
        )
        db_session.add(existing)
        await db_session.flush()

        pipeline = Pipeline(db_session)

        # Same content hash should be duplicate
        is_dup = await pipeline._is_duplicate(channel.id, "different-id", content_hash)
        assert is_dup is True

    @pytest.mark.asyncio
    async def test_match_keyword_rule(self, db_session: AsyncSession):
        """Test keyword rule matching."""
        pipeline = Pipeline(db_session)

        rule = Rule(
            name="Test",
            rule_type=RuleType.KEYWORD,
            pattern="kürzung, streichung, haushalt",
        )

        item_match = Item(
            channel_id=1,
            external_id="1",
            title="Haushaltskürzung angekündigt",
            content="Die Regierung plant Kürzungen",
            url="https://example.com",
            published_at=utcnow(),
            content_hash="hash1",
        )

        item_no_match = Item(
            channel_id=1,
            external_id="2",
            title="Wetter morgen",
            content="Es wird sonnig",
            url="https://example.com",
            published_at=utcnow(),
            content_hash="hash2",
        )

        assert pipeline._match_rule(rule, item_match) is True
        assert pipeline._match_rule(rule, item_no_match) is False

    @pytest.mark.asyncio
    async def test_match_regex_rule(self, db_session: AsyncSession):
        """Test regex rule matching."""
        pipeline = Pipeline(db_session)

        rule = Rule(
            name="Budget Pattern",
            rule_type=RuleType.REGEX,
            pattern=r"\d+\s*(million|mio|mrd).*euro",
        )

        item_match = Item(
            channel_id=1,
            external_id="1",
            title="Budget News",
            content="Das Projekt kostet 50 Millionen Euro",
            url="https://example.com",
            published_at=utcnow(),
            content_hash="hash1",
        )

        item_no_match = Item(
            channel_id=1,
            external_id="2",
            title="Other News",
            content="Keine Zahlen hier",
            url="https://example.com",
            published_at=utcnow(),
            content_hash="hash2",
        )

        assert pipeline._match_rule(rule, item_match) is True
        assert pipeline._match_rule(rule, item_no_match) is False

    @pytest.mark.asyncio
    async def test_score_to_priority(self, db_session: AsyncSession):
        """Test score to priority conversion."""
        pipeline = Pipeline(db_session)

        assert pipeline._score_to_priority(95) == Priority.HIGH
        assert pipeline._score_to_priority(90) == Priority.HIGH
        assert pipeline._score_to_priority(85) == Priority.MEDIUM
        assert pipeline._score_to_priority(70) == Priority.MEDIUM
        assert pipeline._score_to_priority(60) == Priority.LOW
        assert pipeline._score_to_priority(51) == Priority.LOW
        assert pipeline._score_to_priority(50) == Priority.NONE
        assert pipeline._score_to_priority(0) == Priority.NONE

    @pytest.mark.asyncio
    async def test_process_items(self, db_session: AsyncSession):
        """Test full pipeline processing."""
        source = Source(name="Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)

        rule = Rule(
            name="Urgent",
            rule_type=RuleType.KEYWORD,
            pattern="dringend, wichtig",
            priority_boost=30,
            enabled=True,
        )
        db_session.add(rule)
        await db_session.flush()

        raw_items = [
            RawItem(
                external_id="1",
                title="Wichtige Meldung",
                content="Das ist dringend",
                url="https://example.com/1",
            ),
            RawItem(
                external_id="2",
                title="Normale Meldung",
                content="Nichts besonderes",
                url="https://example.com/2",
            ),
        ]

        pipeline = Pipeline(db_session)
        new_items = await pipeline.process(raw_items, channel)

        assert len(new_items) == 2

        # First item should have higher priority due to rule match
        urgent_item = next(i for i in new_items if i.external_id == "1")
        normal_item = next(i for i in new_items if i.external_id == "2")

        assert urgent_item.priority_score > normal_item.priority_score
        assert urgent_item.priority in [Priority.HIGH, Priority.MEDIUM]

    @pytest.mark.asyncio
    async def test_process_skips_duplicates(self, db_session: AsyncSession):
        """Test that duplicates are skipped."""
        source = Source(name="Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)
        await db_session.flush()

        # Create existing item
        existing = Item(
            channel_id=channel.id,
            external_id="existing-1",
            title="Existing",
            content="Content",
            url="https://example.com",
            published_at=utcnow(),
            content_hash="hash1",
        )
        db_session.add(existing)
        await db_session.flush()

        raw_items = [
            RawItem(
                external_id="existing-1",  # Duplicate
                title="Existing",
                content="Content",
                url="https://example.com",
            ),
            RawItem(
                external_id="new-1",  # New
                title="New Article",
                content="New content",
                url="https://example.com/new",
            ),
        ]

        pipeline = Pipeline(db_session)
        new_items = await pipeline.process(raw_items, channel)

        assert len(new_items) == 1
        assert new_items[0].external_id == "new-1"


class TestPipelineEmbeddingsGate:
    """Pipeline must consult embeddings_allowed() before any classifier call."""

    @staticmethod
    def _stub_filter() -> AsyncMock:
        rf = AsyncMock()
        rf.find_duplicates = AsyncMock(return_value=[])
        rf.should_process = AsyncMock(return_value=(True, {"relevance_confidence": 0.5}))
        rf.index_items_batch = AsyncMock(return_value=0)
        return rf

    @staticmethod
    async def _seed_channel(db: AsyncSession) -> Channel:
        source = Source(name="GateTest")
        db.add(source)
        await db.flush()
        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db.add(channel)
        await db.flush()
        return channel

    @pytest.mark.asyncio
    async def test_gate_closed_skips_all_embedding_calls(self, db_session: AsyncSession):
        channel = await self._seed_channel(db_session)
        rf = self._stub_filter()
        raw_items = [
            RawItem(
                external_id="gate-1",
                title="Test 1",
                content="Body 1",
                url="https://example.com/gate-1",
            ),
        ]

        pipeline = Pipeline(db_session, relevance_filter=rf)
        with patch(
            "services.embeddings_gate.embeddings_allowed",
            new=AsyncMock(return_value=(False, "out_of_hours_gpu1_down")),
        ):
            new_items = await pipeline.process(raw_items, channel)

        assert len(new_items) == 1
        rf.find_duplicates.assert_not_awaited()
        rf.should_process.assert_not_awaited()
        rf.index_items_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gate_open_runs_embedding_calls(self, db_session: AsyncSession):
        channel = await self._seed_channel(db_session)
        rf = self._stub_filter()
        raw_items = [
            RawItem(
                external_id="gate-open-1",
                title="Test open",
                content="Body open",
                url="https://example.com/gate-open-1",
            ),
        ]

        pipeline = Pipeline(db_session, relevance_filter=rf)
        with patch(
            "services.embeddings_gate.embeddings_allowed",
            new=AsyncMock(return_value=(True, "in_hours")),
        ):
            new_items = await pipeline.process(raw_items, channel)

        assert len(new_items) == 1
        rf.find_duplicates.assert_awaited()
        rf.should_process.assert_awaited()
        rf.index_items_batch.assert_awaited()

    @pytest.mark.asyncio
    async def test_training_mode_skips_gate_and_filter(self, db_session: AsyncSession):
        """training_mode bypasses the relevance filter entirely; gate is never consulted."""
        channel = await self._seed_channel(db_session)
        rf = self._stub_filter()
        raw_items = [
            RawItem(
                external_id="train-1",
                title="Training item",
                content="Body",
                url="https://example.com/train-1",
            ),
        ]

        gate_mock = AsyncMock(return_value=(True, "in_hours"))
        pipeline = Pipeline(db_session, relevance_filter=rf, training_mode=True)
        with patch("services.embeddings_gate.embeddings_allowed", new=gate_mock):
            await pipeline.process(raw_items, channel)

        gate_mock.assert_not_awaited()
        rf.find_duplicates.assert_not_awaited()
        rf.should_process.assert_not_awaited()
        rf.index_items_batch.assert_not_awaited()


class TestPipelineCommitsBeforeIndexing:
    """#189: new items are committed before vector indexing, so a fetch timeout
    that cancels the pipeline during indexing no longer discards the cycle."""

    @pytest.mark.asyncio
    async def test_items_survive_cancellation_during_indexing(
        self, db_engine, db_session: AsyncSession
    ):
        import asyncio

        from sqlalchemy import func, select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        source = Source(name="CommitTest")
        db_session.add(source)
        await db_session.flush()
        channel = Channel(source_id=source.id, connector_type=ConnectorType.RSS, config={})
        db_session.add(channel)
        await db_session.flush()

        rf = AsyncMock()
        rf.find_duplicates = AsyncMock(return_value=[])
        rf.should_process = AsyncMock(return_value=(True, {"relevance_confidence": 0.5}))
        rf.index_items_batch = AsyncMock(side_effect=asyncio.CancelledError())

        pipeline = Pipeline(db_session, relevance_filter=rf)
        raw = [RawItem(external_id="c-1", title="T", content="B", url="https://example.com/c-1")]
        with patch(
            "services.embeddings_gate.embeddings_allowed",
            new=AsyncMock(return_value=(True, None)),
        ):
            with pytest.raises(asyncio.CancelledError):
                await pipeline.process(raw, channel)
        await db_session.rollback()

        async with async_sessionmaker(db_engine, expire_on_commit=False)() as other:
            count = await other.scalar(
                select(func.count()).select_from(Item).where(Item.channel_id == channel.id)
            )
        assert count == 1
        rf.index_items_batch.assert_awaited_once()


class TestPipelineDuplicateLinking:
    """Duplicate linking rules: links always target the top item of a group, and
    a duplicate candidate alone never forces an item through the LLM."""

    TITLE = "Vielen Kitas fehlen Fachkräfte - Mittelhessen"
    SIMILAR_TITLE = "Kinderbetreuung: Vielen Kitas fehlen Fachkräfte | DPA"
    OTHER_TITLE = "Landtag debattiert über den Haushalt"

    @staticmethod
    async def _seed(db: AsyncSession) -> tuple[Channel, Channel]:
        """Two channels: one holding existing items, one being fetched."""
        source = Source(name="DupLinkTest")
        db.add(source)
        await db.flush()
        existing_channel = Channel(
            source_id=source.id, connector_type=ConnectorType.RSS, config={"n": 1}
        )
        fetch_channel = Channel(
            source_id=source.id, connector_type=ConnectorType.RSS, config={"n": 2}
        )
        db.add_all([existing_channel, fetch_channel])
        await db.flush()
        return existing_channel, fetch_channel

    @staticmethod
    async def _existing_item(
        db: AsyncSession,
        channel: Channel,
        name: str,
        title: str = "Existing",
        similar_to_id: int | None = None,
        priority: Priority = Priority.HIGH,
    ) -> Item:
        # HIGH by default so the existing top outranks whatever the new item
        # gets and the tests below can assert the link target; the rebalance
        # tests pass a lower priority explicitly.
        item = Item(
            channel_id=channel.id,
            external_id=name,
            title=title,
            content=f"Existing content {name}",
            url=f"https://existing.example.com/{name}",
            published_at=utcnow(),
            content_hash=f"existing-hash-{name}",
            similar_to_id=similar_to_id,
            priority=priority,
        )
        db.add(item)
        await db.flush()
        return item

    @staticmethod
    def _filter(matches: list[dict], confidence: float = 0.6) -> AsyncMock:
        rf = AsyncMock()
        rf.find_duplicates = AsyncMock(return_value=matches)
        rf.should_process = AsyncMock(
            return_value=(confidence >= 0.25, {"relevance_confidence": confidence})
        )
        rf.index_items_batch = AsyncMock(return_value=0)
        return rf

    @staticmethod
    async def _run(db: AsyncSession, channel: Channel, rf: AsyncMock, raw_items: list[RawItem]):
        """Run the pipeline with the embeddings gate open; returns (items, enqueue mock)."""
        enqueue = AsyncMock()
        pipeline = Pipeline(db, relevance_filter=rf)
        with (
            patch(
                "services.embeddings_gate.embeddings_allowed",
                new=AsyncMock(return_value=(True, "in_hours")),
            ),
            patch("services.llm_worker.enqueue_fresh_item", new=enqueue),
        ):
            items = await pipeline.process(raw_items, channel)
        return items, enqueue

    def _raw(self, name: str = "new-1", title: str | None = None, url: str | None = None) -> RawItem:
        return RawItem(
            external_id=name,
            title=title or self.TITLE,
            content=f"Body of {name}",
            url=url or f"https://example.com/{name}",
        )

    @pytest.mark.asyncio
    async def test_gate_skipped_item_with_candidate_is_not_sent_to_llm(
        self, db_session: AsyncSession
    ):
        """Classifier gate says skip: a duplicate candidate must not override it."""
        existing_channel, channel = await self._seed(db_session)
        other = await self._existing_item(db_session, existing_channel, "other", self.OTHER_TITLE)
        rf = self._filter(
            [{"id": str(other.id), "title": self.OTHER_TITLE, "score": 0.80}],
            confidence=0.1,  # below 0.25 -> skip_llm
        )

        items, enqueue = await self._run(db_session, channel, rf, [self._raw()])

        assert len(items) == 1
        item = items[0]
        assert item.needs_llm_processing is False
        assert item.similar_to_id is None
        # Candidate is still recorded, it is just not a reason to call the LLM
        assert item.metadata_["duplicate_candidate"]["candidate_id"] == other.id
        enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_relevant_item_with_candidate_goes_to_llm(self, db_session: AsyncSession):
        existing_channel, channel = await self._seed(db_session)
        other = await self._existing_item(db_session, existing_channel, "other", self.OTHER_TITLE)
        rf = self._filter(
            [{"id": str(other.id), "title": self.OTHER_TITLE, "score": 0.80}],
            confidence=0.6,
        )

        items, enqueue = await self._run(db_session, channel, rf, [self._raw()])

        item = items[0]
        assert item.needs_llm_processing is True
        assert item.similar_to_id is None
        assert item.metadata_["duplicate_candidate"] == {
            "candidate_id": other.id,
            "candidate_title": self.OTHER_TITLE,
            "similarity_score": 0.80,
        }
        assert "duplicate_method" not in item.metadata_
        enqueue.assert_awaited_once_with(item.id)

    @pytest.mark.asyncio
    async def test_semantic_match_on_copy_links_to_top_item(self, db_session: AsyncSession):
        """The best match is itself a copy: link to its top item, not to the copy."""
        existing_channel, channel = await self._seed(db_session)
        top = await self._existing_item(db_session, existing_channel, "top")
        copy = await self._existing_item(db_session, existing_channel, "copy", similar_to_id=top.id)
        rf = self._filter([{"id": str(copy.id), "title": self.OTHER_TITLE, "score": 0.93}])

        items, _ = await self._run(db_session, channel, rf, [self._raw()])

        item = items[0]
        assert item.similar_to_id == top.id
        assert item.metadata_["duplicate_method"] == "semantic"
        assert item.metadata_["duplicate_score"] == 0.93
        assert "duplicate_candidate" not in item.metadata_

    @pytest.mark.asyncio
    async def test_mid_score_with_similar_title_links(self, db_session: AsyncSession):
        existing_channel, channel = await self._seed(db_session)
        match = await self._existing_item(db_session, existing_channel, "m", self.SIMILAR_TITLE)
        rf = self._filter([{"id": str(match.id), "title": self.SIMILAR_TITLE, "score": 0.80}])

        items, _ = await self._run(db_session, channel, rf, [self._raw()])

        item = items[0]
        assert item.similar_to_id == match.id
        assert item.metadata_["duplicate_method"] == "semantic_title"
        assert item.metadata_["duplicate_score"] == 0.80
        assert "duplicate_candidate" not in item.metadata_

    @pytest.mark.asyncio
    async def test_old_confirmed_threshold_no_longer_links(self, db_session: AsyncSession):
        """0.80 used to auto-link (old threshold 0.75); with an unrelated title it
        is now only a candidate."""
        existing_channel, channel = await self._seed(db_session)
        other = await self._existing_item(db_session, existing_channel, "other", self.OTHER_TITLE)
        rf = self._filter([{"id": str(other.id), "title": self.OTHER_TITLE, "score": 0.80}])

        items, _ = await self._run(db_session, channel, rf, [self._raw()])

        assert items[0].similar_to_id is None
        assert rf.find_duplicates.await_args.kwargs["threshold"] == 0.75

    @pytest.mark.asyncio
    async def test_matches_for_deleted_items_are_dropped(self, db_session: AsyncSession):
        """Vector index out of sync: the best match no longer exists, so the next
        existing match decides."""
        existing_channel, channel = await self._seed(db_session)
        other = await self._existing_item(db_session, existing_channel, "other", self.OTHER_TITLE)
        rf = self._filter(
            [
                {"id": "987654", "title": "Gone", "score": 0.97},
                {"id": str(other.id), "title": self.OTHER_TITLE, "score": 0.79},
            ]
        )

        items, _ = await self._run(db_session, channel, rf, [self._raw()])

        item = items[0]
        assert item.similar_to_id is None
        assert item.metadata_["duplicate_candidate"]["candidate_id"] == other.id

    @pytest.mark.asyncio
    async def test_url_match_on_copy_links_to_top_item(self, db_session: AsyncSession):
        existing_channel, channel = await self._seed(db_session)
        top = await self._existing_item(db_session, existing_channel, "top")
        copy = await self._existing_item(db_session, existing_channel, "copy", similar_to_id=top.id)
        rf = self._filter([])

        items, _ = await self._run(db_session, channel, rf, [self._raw(url=copy.url)])

        assert items[0].similar_to_id == top.id
        # URL match settles it; no semantic lookup needed
        rf.find_duplicates.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_intra_batch_duplicate_links_to_top_item_of_batch_primary(
        self, db_session: AsyncSession
    ):
        """The batch primary is itself a copy (URL match): the second batch item
        must link to the existing top item, not to the batch primary."""
        existing_channel, channel = await self._seed(db_session)
        top = await self._existing_item(db_session, existing_channel, "top")
        rf = self._filter([])

        items, _ = await self._run(
            db_session,
            channel,
            rf,
            [
                self._raw("batch-1", title=self.TITLE, url=top.url),
                self._raw("batch-2", title=self.SIMILAR_TITLE),
            ],
        )

        first, second = items
        assert first.similar_to_id == top.id
        assert second.similar_to_id == top.id

    @pytest.mark.asyncio
    async def test_intra_batch_duplicate_links_to_batch_primary(self, db_session: AsyncSession):
        _, channel = await self._seed(db_session)
        rf = self._filter([])

        items, _ = await self._run(
            db_session,
            channel,
            rf,
            [
                self._raw("batch-1", title=self.TITLE),
                self._raw("batch-2", title=self.SIMILAR_TITLE),
                self._raw("batch-3", title="Duma-Wahl: Auf einen Blick"),
                self._raw("batch-4", title="Wildtiere: Auf einen Blick"),
            ],
        )

        first, second, rubric1, rubric2 = items
        assert first.similar_to_id is None
        assert second.similar_to_id == first.id
        # Rubric titles share only "Auf einen Blick" and stay separate
        assert rubric1.similar_to_id is None
        assert rubric2.similar_to_id is None

    # --- every link ends with a rebalance of the group it joined ---

    @staticmethod
    async def _links(db: AsyncSession) -> dict[int, int | None]:
        from sqlalchemy import select

        rows = await db.execute(select(Item.id, Item.similar_to_id))
        return {row.id: row.similar_to_id for row in rows}

    @staticmethod
    async def _high_rule(db: AsyncSession, pattern: str) -> None:
        db.add(
            Rule(
                name="Rebalance rule",
                rule_type=RuleType.KEYWORD,
                pattern=pattern,
                target_priority=Priority.HIGH,
                enabled=True,
                order=0,
            )
        )
        await db.flush()

    @pytest.mark.asyncio
    async def test_url_linked_copy_with_higher_rule_priority_becomes_top(
        self, db_session: AsyncSession
    ):
        """New item has rule priority HIGH and links to a NONE top: it must not
        be hidden under the irrelevant top."""
        existing_channel, channel = await self._seed(db_session)
        top = await self._existing_item(db_session, existing_channel, "top", priority=Priority.NONE)
        copy = await self._existing_item(
            db_session, existing_channel, "copy", similar_to_id=top.id, priority=Priority.NONE
        )
        await self._high_rule(db_session, "Fachkräfte")

        items, _ = await self._run(db_session, channel, None, [self._raw(url=copy.url)])

        item = items[0]
        assert item.priority == Priority.HIGH
        assert item.similar_to_id is None
        assert await self._links(db_session) == {item.id: None, top.id: item.id, copy.id: item.id}

    @pytest.mark.asyncio
    async def test_semantically_linked_copy_with_higher_priority_becomes_top(
        self, db_session: AsyncSession
    ):
        existing_channel, channel = await self._seed(db_session)
        top = await self._existing_item(db_session, existing_channel, "top", priority=Priority.NONE)
        # classifier confidence 0.6 -> the new item is MEDIUM
        rf = self._filter([{"id": str(top.id), "title": self.OTHER_TITLE, "score": 0.93}])

        items, _ = await self._run(db_session, channel, rf, [self._raw()])

        item = items[0]
        assert item.priority == Priority.MEDIUM
        assert item.similar_to_id is None
        assert item.metadata_["duplicate_method"] == "semantic"
        assert await self._links(db_session) == {item.id: None, top.id: item.id}

    @pytest.mark.asyncio
    async def test_linked_copy_with_equal_priority_stays_a_copy(self, db_session: AsyncSession):
        existing_channel, channel = await self._seed(db_session)
        top = await self._existing_item(
            db_session, existing_channel, "top", priority=Priority.MEDIUM
        )
        rf = self._filter([{"id": str(top.id), "title": self.OTHER_TITLE, "score": 0.93}])

        items, _ = await self._run(db_session, channel, rf, [self._raw()])

        assert items[0].priority == Priority.MEDIUM
        assert await self._links(db_session) == {top.id: None, items[0].id: top.id}

    @pytest.mark.asyncio
    async def test_intra_batch_copy_with_higher_priority_becomes_top(
        self, db_session: AsyncSession
    ):
        """Second batch item is a copy of the first but outranks it (rule HIGH)."""
        _, channel = await self._seed(db_session)
        await self._high_rule(db_session, "Kinderbetreuung")

        items, _ = await self._run(
            db_session,
            channel,
            None,
            [
                self._raw("batch-1", title=self.TITLE),
                self._raw("batch-2", title=self.SIMILAR_TITLE),
            ],
        )

        first, second = items
        assert second.priority == Priority.HIGH
        assert first.priority != Priority.HIGH
        assert await self._links(db_session) == {second.id: None, first.id: second.id}

    @pytest.mark.asyncio
    async def test_rebalance_failure_does_not_fail_the_fetch(self, db_session: AsyncSession):
        existing_channel, channel = await self._seed(db_session)
        top = await self._existing_item(db_session, existing_channel, "top", priority=Priority.NONE)
        rf = self._filter([{"id": str(top.id), "title": self.OTHER_TITLE, "score": 0.93}])

        with patch(
            "services.dedup_groups.rebalance_group",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            items, enqueue = await self._run(db_session, channel, rf, [self._raw()])

        # The item is stored and linked as before; only the promotion is missing
        item = items[0]
        assert await self._links(db_session) == {top.id: None, item.id: top.id}
        enqueue.assert_awaited_once_with(item.id)
