"""Tests for duplicate-group rules and helpers (services/dedup_groups.py)."""

from unittest.mock import patch

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database import utcnow
from models import Channel, ConnectorType, Item, Priority, Source
from services.dedup_groups import (
    DUPLICATE_THRESHOLD_CONFIRMED,
    DUPLICATE_THRESHOLD_MAYBE,
    GROUP_REBALANCE_LOCK_KEY,
    PRIORITY_RANK,
    LinkDecision,
    decide_semantic_link,
    pick_top,
    rebalance_group,
    rebalance_groups_guarded,
    resolve_primary,
    titles_similar,
)


async def _seed_channel(db: AsyncSession) -> Channel:
    source = Source(name="DedupGroups")
    db.add(source)
    await db.flush()
    channel = Channel(source_id=source.id, connector_type=ConnectorType.RSS, config={})
    db.add(channel)
    await db.flush()
    return channel


async def _add_item(
    db: AsyncSession,
    channel: Channel,
    name: str,
    priority: Priority = Priority.NONE,
    similar_to_id: int | None = None,
    is_archived: bool = False,
) -> Item:
    item = Item(
        channel_id=channel.id,
        external_id=name,
        title=f"Title {name}",
        content=f"Content {name}",
        url=f"https://example.com/{name}",
        published_at=utcnow(),
        content_hash=f"hash-{name}",
        priority=priority,
        similar_to_id=similar_to_id,
        is_archived=is_archived,
    )
    db.add(item)
    await db.flush()
    return item


async def _links(db: AsyncSession) -> dict[int, int | None]:
    """Current id -> similar_to_id straight from the database."""
    rows = await db.execute(select(Item.id, Item.similar_to_id))
    return {row.id: row.similar_to_id for row in rows}


class TestConstants:
    def test_default_thresholds(self):
        assert DUPLICATE_THRESHOLD_CONFIRMED == 0.88
        assert DUPLICATE_THRESHOLD_MAYBE == 0.75

    def test_priority_rank(self):
        assert PRIORITY_RANK == {"high": 3, "medium": 2, "low": 1, "none": 0}

    def test_pipeline_keeps_legacy_names(self):
        """Existing imports from services.pipeline keep working."""
        from services import pipeline

        assert pipeline._titles_similar is titles_similar
        assert pipeline.DUPLICATE_THRESHOLD_CONFIRMED == DUPLICATE_THRESHOLD_CONFIRMED
        assert pipeline.DUPLICATE_THRESHOLD_MAYBE == DUPLICATE_THRESHOLD_MAYBE


class TestTitlesSimilar:
    def test_rubric_titles_are_not_similar(self):
        """A shared rubric ("Auf einen Blick") must not link unrelated articles."""
        assert not titles_similar("Duma-Wahl: Auf einen Blick", "Wildtiere: Auf einen Blick")

    def test_same_headline_with_different_decoration(self):
        assert titles_similar(
            "Vielen Kitas fehlen Fachkräfte - Mittelhessen",
            "Kinderbetreuung: Vielen Kitas fehlen Fachkräfte | DPA",
        )

    def test_case_insensitive(self):
        assert titles_similar(
            "VIELEN KITAS FEHLEN FACHKRÄFTE", "vielen kitas fehlen fachkräfte"
        )

    def test_fewer_than_four_shared_words(self):
        """Identical but short titles do not carry enough signal."""
        assert not titles_similar("Streik in Hessen", "Streik in Hessen")

    def test_short_title_contained_in_long_title(self):
        """Four shared words, but they are less than half of the longer title."""
        short = "Landtag beschließt neues Kitagesetz"
        long = (
            "Landtag beschließt neues Kitagesetz während Opposition scharfe Kritik "
            "übt und Verbände weitere Nachbesserungen fordern"
        )
        assert not titles_similar(short, long)

    def test_empty_titles(self):
        assert not titles_similar("", "Vielen Kitas fehlen Fachkräfte")
        assert not titles_similar("", "")


class TestDecideSemanticLink:
    TITLE = "Vielen Kitas fehlen Fachkräfte - Mittelhessen"
    SIMILAR = "Kinderbetreuung: Vielen Kitas fehlen Fachkräfte | DPA"
    DIFFERENT = "Landtag debattiert über den Haushalt"

    def test_high_score_links_as_semantic(self):
        decision = decide_semantic_link(
            self.TITLE, [{"id": "7", "title": self.DIFFERENT, "score": 0.90}]
        )
        assert decision.link_id == 7
        assert decision.score == 0.90
        assert decision.method == "semantic"
        assert decision.candidate is None

    def test_mid_score_with_similar_title_links_as_semantic_title(self):
        decision = decide_semantic_link(
            self.TITLE, [{"id": 7, "title": self.SIMILAR, "score": 0.80}]
        )
        assert decision.link_id == 7
        assert decision.score == 0.80
        assert decision.method == "semantic_title"
        assert decision.candidate is None

    def test_mid_score_with_different_title_is_candidate(self):
        decision = decide_semantic_link(
            self.TITLE, [{"id": "7", "title": self.DIFFERENT, "score": 0.80}]
        )
        assert decision.link_id is None
        assert decision.method is None
        assert decision.candidate == {
            "candidate_id": 7,
            "candidate_title": self.DIFFERENT,
            "similarity_score": 0.80,
        }

    def test_low_score_is_empty(self):
        decision = decide_semantic_link(
            self.TITLE, [{"id": "7", "title": self.SIMILAR, "score": 0.70}]
        )
        assert decision == LinkDecision()

    def test_no_matches_is_empty(self):
        assert decide_semantic_link(self.TITLE, []) == LinkDecision()

    def test_threshold_boundaries(self):
        at_confirmed = decide_semantic_link(
            self.TITLE, [{"id": 1, "title": self.DIFFERENT, "score": DUPLICATE_THRESHOLD_CONFIRMED}]
        )
        assert at_confirmed.method == "semantic"

        at_maybe = decide_semantic_link(
            self.TITLE, [{"id": 1, "title": self.DIFFERENT, "score": DUPLICATE_THRESHOLD_MAYBE}]
        )
        assert at_maybe.link_id is None
        assert at_maybe.candidate["candidate_id"] == 1

    def test_matches_are_ranked_by_score_regardless_of_order(self):
        decision = decide_semantic_link(
            self.TITLE,
            [
                {"id": 1, "title": self.DIFFERENT, "score": 0.76},
                {"id": 2, "title": self.DIFFERENT, "score": 0.93},
                {"id": 3, "title": self.DIFFERENT, "score": 0.89},
            ],
        )
        assert decision.link_id == 2
        assert decision.method == "semantic"

    def test_lower_match_with_similar_title_beats_better_unlinked_match(self):
        """The best match fails both rules, the next one links via its title."""
        decision = decide_semantic_link(
            self.TITLE,
            [
                {"id": 1, "title": self.DIFFERENT, "score": 0.85},
                {"id": 2, "title": self.SIMILAR, "score": 0.78},
            ],
        )
        assert decision.link_id == 2
        assert decision.method == "semantic_title"
        assert decision.candidate is None

    def test_candidate_is_the_best_match(self):
        decision = decide_semantic_link(
            self.TITLE,
            [
                {"id": 1, "title": self.DIFFERENT, "score": 0.77},
                {"id": 2, "title": self.DIFFERENT, "score": 0.84},
                {"id": 3, "title": self.SIMILAR, "score": 0.60},
            ],
        )
        assert decision.link_id is None
        assert decision.candidate["candidate_id"] == 2
        assert decision.candidate["similarity_score"] == 0.84

    def test_title_comparison_ignores_case(self):
        decision = decide_semantic_link(
            self.TITLE.upper(), [{"id": 7, "title": self.SIMILAR.lower(), "score": 0.80}]
        )
        assert decision.method == "semantic_title"


class TestPickTop:
    def test_highest_priority_wins(self):
        members = [(1, "none"), (2, "low"), (3, "high"), (4, "medium")]
        assert pick_top(members, current_top_id=1) == 3

    def test_tie_keeps_current_top(self):
        members = [(1, "medium"), (2, "medium"), (3, "low")]
        assert pick_top(members, current_top_id=2) == 2

    def test_tie_without_current_top_among_best_picks_lowest_id(self):
        members = [(5, "high"), (3, "high"), (1, "low")]
        assert pick_top(members, current_top_id=1) == 3
        assert pick_top(members, current_top_id=None) == 3

    def test_all_irrelevant_keeps_current_top(self):
        assert pick_top([(1, "none"), (2, "none")], current_top_id=2) == 2

    def test_accepts_priority_enum(self):
        members = [(1, Priority.NONE), (2, Priority.MEDIUM)]
        assert pick_top(members, current_top_id=1) == 2

    def test_single_member(self):
        assert pick_top([(9, "none")], current_top_id=None) == 9


class TestResolvePrimary:
    @pytest.mark.asyncio
    async def test_top_item_resolves_to_itself(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top")

        assert await resolve_primary(db_session, top.id) == top.id

    @pytest.mark.asyncio
    async def test_follows_chain_to_top(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top")
        copy1 = await _add_item(db_session, channel, "c1", similar_to_id=top.id)
        copy2 = await _add_item(db_session, channel, "c2", similar_to_id=copy1.id)
        copy3 = await _add_item(db_session, channel, "c3", similar_to_id=copy2.id)

        assert await resolve_primary(db_session, copy3.id) == top.id
        assert await resolve_primary(db_session, copy1.id) == top.id

    @pytest.mark.asyncio
    async def test_cycle_returns_lowest_id(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        a = await _add_item(db_session, channel, "a")
        b = await _add_item(db_session, channel, "b", similar_to_id=a.id)
        c = await _add_item(db_session, channel, "c", similar_to_id=b.id)
        await db_session.execute(
            update(Item).where(Item.id == a.id).values(similar_to_id=c.id)
        )

        lowest = min(a.id, b.id, c.id)
        assert await resolve_primary(db_session, a.id) == lowest
        assert await resolve_primary(db_session, b.id) == lowest
        assert await resolve_primary(db_session, c.id) == lowest

    @pytest.mark.asyncio
    async def test_self_reference(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        a = await _add_item(db_session, channel, "a")
        await db_session.execute(
            update(Item).where(Item.id == a.id).values(similar_to_id=a.id)
        )

        assert await resolve_primary(db_session, a.id) == a.id

    @pytest.mark.asyncio
    async def test_missing_item(self, db_session: AsyncSession):
        assert await resolve_primary(db_session, 999_999) is None


class TestRebalanceGroup:
    @pytest.mark.asyncio
    async def test_relevant_copy_is_promoted_and_chain_flattened(
        self, db_session: AsyncSession
    ):
        """A relevant copy under an irrelevant top becomes the top; the legacy
        copy -> copy chain becomes a star around it."""
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top", Priority.NONE)
        copy1 = await _add_item(db_session, channel, "c1", Priority.NONE, top.id)
        relevant = await _add_item(db_session, channel, "c2", Priority.MEDIUM, copy1.id)
        copy3 = await _add_item(db_session, channel, "c3", Priority.LOW, relevant.id)
        bystander = await _add_item(db_session, channel, "other", Priority.HIGH)

        result = await rebalance_group(db_session, copy3.id)

        assert result == relevant.id
        assert await _links(db_session) == {
            relevant.id: None,
            top.id: relevant.id,
            copy1.id: relevant.id,
            copy3.id: relevant.id,
            bystander.id: None,
        }

    @pytest.mark.asyncio
    async def test_same_result_from_any_member(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top", Priority.NONE)
        relevant = await _add_item(db_session, channel, "c1", Priority.HIGH, top.id)

        assert await rebalance_group(db_session, top.id) == relevant.id
        assert await _links(db_session) == {relevant.id: None, top.id: relevant.id}

    @pytest.mark.asyncio
    async def test_no_change_returns_none(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top", Priority.HIGH)
        copy1 = await _add_item(db_session, channel, "c1", Priority.MEDIUM, top.id)
        copy2 = await _add_item(db_session, channel, "c2", Priority.NONE, top.id)

        assert await rebalance_group(db_session, copy1.id) is None
        assert await _links(db_session) == {top.id: None, copy1.id: top.id, copy2.id: top.id}

    @pytest.mark.asyncio
    async def test_tie_keeps_current_top(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top", Priority.MEDIUM)
        copy1 = await _add_item(db_session, channel, "c1", Priority.MEDIUM, top.id)

        assert await rebalance_group(db_session, copy1.id) is None
        assert await _links(db_session) == {top.id: None, copy1.id: top.id}

    @pytest.mark.asyncio
    async def test_chain_is_flattened_even_when_top_stays(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top", Priority.HIGH)
        copy1 = await _add_item(db_session, channel, "c1", Priority.NONE, top.id)
        copy2 = await _add_item(db_session, channel, "c2", Priority.NONE, copy1.id)

        assert await rebalance_group(db_session, copy2.id) == top.id
        assert await _links(db_session) == {top.id: None, copy1.id: top.id, copy2.id: top.id}

    @pytest.mark.asyncio
    async def test_single_item_is_untouched(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        item = await _add_item(db_session, channel, "solo", Priority.MEDIUM)

        assert await rebalance_group(db_session, item.id) is None
        assert await _links(db_session) == {item.id: None}

    @pytest.mark.asyncio
    async def test_cycle_is_repaired(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        a = await _add_item(db_session, channel, "a", Priority.NONE)
        b = await _add_item(db_session, channel, "b", Priority.LOW, a.id)
        await db_session.execute(
            update(Item).where(Item.id == a.id).values(similar_to_id=b.id)
        )

        assert await rebalance_group(db_session, a.id) == b.id
        assert await _links(db_session) == {b.id: None, a.id: b.id}

    @pytest.mark.asyncio
    async def test_missing_item(self, db_session: AsyncSession):
        assert await rebalance_group(db_session, 999_999) is None


class TestPickTopArchived:
    """An archived item is hidden from the default feed, so it must not be the
    top while a non-archived member exists."""

    def test_non_archived_beats_archived_with_higher_priority(self):
        members = [(1, "high", True), (2, "low", False)]
        assert pick_top(members, current_top_id=1) == 2

    def test_non_archived_irrelevant_beats_archived_relevant(self):
        members = [(1, "medium", True), (2, "none", False)]
        assert pick_top(members, current_top_id=1) == 2

    def test_priority_decides_among_non_archived(self):
        members = [(1, "high", True), (2, "low", False), (3, "medium", False)]
        assert pick_top(members, current_top_id=2) == 3

    def test_all_archived_falls_back_to_priority(self):
        members = [(1, "low", True), (2, "high", True), (3, "none", True)]
        assert pick_top(members, current_top_id=1) == 2

    def test_tie_keeps_current_top_only_if_among_the_best(self):
        members = [(1, "medium", True), (5, "medium", False), (3, "medium", False)]
        # current top is archived -> not among the best -> lowest id of the best
        assert pick_top(members, current_top_id=1) == 3
        assert pick_top(members, current_top_id=5) == 5

    def test_two_tuples_still_work_and_count_as_not_archived(self):
        assert pick_top([(1, "none"), (2, "high")], current_top_id=1) == 2
        # mixed shapes: the two-tuple is treated as not archived
        assert pick_top([(1, "high", True), (2, "low")], current_top_id=1) == 2

    def test_archived_flag_none_counts_as_not_archived(self):
        assert pick_top([(1, "high", None), (2, "low", True)], current_top_id=2) == 1


class TestRebalanceGroupArchived:
    @pytest.mark.asyncio
    async def test_archived_top_gives_way_to_non_archived_copy(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top", Priority.HIGH, is_archived=True)
        copy1 = await _add_item(db_session, channel, "c1", Priority.LOW, top.id)
        copy2 = await _add_item(db_session, channel, "c2", Priority.NONE, top.id)

        assert await rebalance_group(db_session, top.id) == copy1.id
        assert await _links(db_session) == {copy1.id: None, top.id: copy1.id, copy2.id: copy1.id}

    @pytest.mark.asyncio
    async def test_archived_copy_is_not_promoted_over_non_archived_top(
        self, db_session: AsyncSession
    ):
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top", Priority.LOW)
        copy1 = await _add_item(db_session, channel, "c1", Priority.HIGH, top.id, is_archived=True)

        assert await rebalance_group(db_session, copy1.id) is None
        assert await _links(db_session) == {top.id: None, copy1.id: top.id}

    @pytest.mark.asyncio
    async def test_fully_archived_group_ranks_by_priority(self, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top", Priority.NONE, is_archived=True)
        copy1 = await _add_item(
            db_session, channel, "c1", Priority.MEDIUM, top.id, is_archived=True
        )

        assert await rebalance_group(db_session, top.id) == copy1.id
        assert await _links(db_session) == {copy1.id: None, top.id: copy1.id}


class TestRebalanceLock:
    """rebalance_group serialises on one transaction-scoped advisory lock."""

    @staticmethod
    async def _try_lock(db: AsyncSession) -> bool:
        return await db.scalar(select(func.pg_try_advisory_xact_lock(GROUP_REBALANCE_LOCK_KEY)))

    @pytest.mark.asyncio
    async def test_lock_is_the_first_statement(self, db_session: AsyncSession):
        """The lock comes before any read, so the reads see what another
        rebalance committed while we waited."""
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top", Priority.NONE)
        await _add_item(db_session, channel, "c1", Priority.HIGH, top.id)

        statements: list[str] = []
        real_execute = db_session.execute

        async def recording_execute(statement, *args, **kwargs):
            statements.append(str(statement))
            return await real_execute(statement, *args, **kwargs)

        with patch.object(db_session, "execute", new=recording_execute):
            await rebalance_group(db_session, top.id)

        assert "pg_advisory_xact_lock" in statements[0]
        assert all("pg_advisory_xact_lock" not in stmt for stmt in statements[1:])
        assert len(statements) > 1

    @pytest.mark.asyncio
    async def test_lock_blocks_other_sessions_until_commit(
        self, db_engine, db_session: AsyncSession
    ):
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top", Priority.NONE)
        copy1 = await _add_item(db_session, channel, "c1", Priority.HIGH, top.id)
        await db_session.commit()

        other_maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

        assert await rebalance_group(db_session, top.id) == copy1.id
        async with other_maker() as other:
            assert await self._try_lock(other) is False  # held by db_session

        await db_session.commit()
        async with other_maker() as other:
            assert await self._try_lock(other) is True  # released by the commit
            # ...and the other session now sees the committed star
            assert await _links(other) == {copy1.id: None, top.id: copy1.id}

    @pytest.mark.asyncio
    async def test_lock_is_reentrant_within_one_transaction(self, db_session: AsyncSession):
        """Several rebalances in one transaction (as in the db_session fixture)
        must not block each other."""
        channel = await _seed_channel(db_session)
        top = await _add_item(db_session, channel, "top", Priority.NONE)
        copy1 = await _add_item(db_session, channel, "c1", Priority.HIGH, top.id)

        assert await rebalance_group(db_session, top.id) == copy1.id
        assert await rebalance_group(db_session, top.id) is None
        assert await rebalance_group(db_session, copy1.id) is None

    @pytest.mark.asyncio
    async def test_lock_is_released_on_rollback(self, db_engine, db_session: AsyncSession):
        await rebalance_group(db_session, 999_999)
        await db_session.rollback()

        other_maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with other_maker() as other:
            assert await self._try_lock(other) is True


class TestRebalanceGroupsGuarded:
    @pytest.mark.asyncio
    async def test_rebalances_and_commits_each_group(self, db_engine, db_session: AsyncSession):
        channel = await _seed_channel(db_session)
        top1 = await _add_item(db_session, channel, "t1", Priority.NONE)
        best1 = await _add_item(db_session, channel, "b1", Priority.HIGH, top1.id)
        top2 = await _add_item(db_session, channel, "t2", Priority.LOW)
        best2 = await _add_item(db_session, channel, "b2", Priority.MEDIUM, top2.id)
        settled = await _add_item(db_session, channel, "s", Priority.HIGH)
        await db_session.commit()

        changed = await rebalance_groups_guarded(
            db_session, [top1.id, top1.id, None, best2.id, settled.id], context="test"
        )

        assert changed == 2
        # Committed: a fresh session sees it, and the lock is free again
        other_maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with other_maker() as other:
            assert await _links(other) == {
                best1.id: None,
                top1.id: best1.id,
                best2.id: None,
                top2.id: best2.id,
                settled.id: None,
            }
            assert await other.scalar(
                select(func.pg_try_advisory_xact_lock(GROUP_REBALANCE_LOCK_KEY))
            )

    @pytest.mark.asyncio
    async def test_failure_is_swallowed_and_does_not_stop_the_rest(
        self, db_session: AsyncSession
    ):
        channel = await _seed_channel(db_session)
        top1 = await _add_item(db_session, channel, "t1", Priority.NONE)
        best1 = await _add_item(db_session, channel, "b1", Priority.HIGH, top1.id)
        top2 = await _add_item(db_session, channel, "t2", Priority.NONE)
        best2 = await _add_item(db_session, channel, "b2", Priority.HIGH, top2.id)
        await db_session.commit()

        async def flaky(db, item_id):
            if item_id == top1.id:
                # Fail inside the database so the savepoint really has to recover
                await db.execute(text("SELECT 1 / 0"))
            return await rebalance_group(db, item_id)

        with patch("services.dedup_groups.rebalance_group", new=flaky):
            changed = await rebalance_groups_guarded(db_session, [top1.id, top2.id])

        assert changed == 1
        assert await _links(db_session) == {
            top1.id: None,
            best1.id: top1.id,  # untouched
            best2.id: None,
            top2.id: best2.id,
        }
        # Objects loaded before the failure are still usable (not expired)
        assert top1.title == "Title t1"

    @pytest.mark.asyncio
    async def test_empty_input(self, db_session: AsyncSession):
        assert await rebalance_groups_guarded(db_session, []) == 0
