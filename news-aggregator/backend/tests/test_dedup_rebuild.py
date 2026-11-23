"""Tests for the duplicate-group rebuild (services/dedup_rebuild.py + admin endpoint)."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from contextlib import asynccontextmanager

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database import utcnow
from models import Channel, ConnectorType, Item, Priority, Source
from services import dedup_rebuild
from services.dedup_groups import DUPLICATE_THRESHOLD_MAYBE
from services.dedup_rebuild import (
    METHOD_KEPT,
    METHOD_REBALANCED,
    RUN_LOCK_KEY,
    ContextUnlink,
    LookupFailedError,
    RebuildAlreadyRunning,
    RebuildItem,
    StoredLink,
    plan_groups,
    rebuild_groups,
    summarize,
)

T0 = datetime(2026, 11, 1, 12, 0, 0)


def make_item(item_id: int, **kwargs) -> RebuildItem:
    values = {
        "id": item_id,
        "title": f"Item {item_id}",
        "url": f"https://example.com/article/{item_id}",
        "content_hash": f"hash-{item_id}",
        "channel_id": 1,
        "priority": "none",
        "similar_to_id": None,
        "fetched_at": T0 + timedelta(minutes=item_id),
    }
    values.update(kwargs)
    return RebuildItem(**values)


def match(item_id: int, score: float, title: str = "") -> dict:
    return {"id": str(item_id), "title": title, "score": score}


class FakeClassifier:
    """``answers``: item id -> matches / None (not indexed) / an exception (call failed).

    Items without an answer get ``default`` (no match).
    """

    def __init__(self, answers: dict | None = None, default=()):
        self.answers = answers or {}
        self.default = default
        self.calls: list[tuple[int, list[int]]] = []
        self.options: list[dict] = []
        self.closed = False

    async def find_duplicates_by_id(
        self, item_id, candidate_ids, threshold=0.75, n_results=5, raise_on_error=False
    ):
        self.calls.append((item_id, list(candidate_ids)))
        self.options.append(
            {"threshold": threshold, "n_results": n_results, "raise_on_error": raise_on_error}
        )
        answer = self.answers.get(item_id, self.default)
        if isinstance(answer, Exception):
            raise answer
        return None if answer is None else list(answer)

    async def close(self):
        self.closed = True


# =============================================================================
# Decision part: no database
# =============================================================================


class TestPlanGroups:
    @pytest.mark.asyncio
    async def test_chain_member_without_match_becomes_its_own_top(self):
        """A(none) <- B(none) <- C(high): C matches nothing and must be visible again."""
        items = [
            make_item(1),
            make_item(2, similar_to_id=1),
            make_item(3, similar_to_id=2, priority="high"),
        ]
        classifier = FakeClassifier({2: [match(1, 0.95)], 3: []})

        plan = await plan_groups(items, classifier)

        assert plan.new_top == {1: None, 2: 1, 3: None}
        assert plan.link_info[2].method == "semantic"
        assert plan.link_info[2].score == pytest.approx(0.95)
        assert plan.promoted_groups == 0

        summary = summarize(items, plan)["summary"]
        assert summary["items"] == 3
        assert summary["groups"] == 1
        assert summary["links_before"] == 2
        assert summary["links_after"] == 1
        assert summary["links_removed"] == 1
        assert summary["links_added"] == 0
        assert summary["links_repointed"] == 0
        assert summary["relevant_hidden_before"] == 1
        assert summary["relevant_hidden_after"] == 0
        assert summary["samples"] == [
            {
                "id": 3,
                "title": "Item 3",
                "old_target": 2,
                "new_target": None,
                "score": None,
                "method": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_copy_of_copy_is_linked_to_the_top_item(self):
        """C matches the copy B: the result is a star, never copy -> copy."""
        items = [make_item(1), make_item(2), make_item(3)]
        classifier = FakeClassifier({2: [match(1, 0.9)], 3: [match(2, 0.9)]})

        plan = await plan_groups(items, classifier)

        assert plan.new_top == {1: None, 2: 1, 3: 1}

    @pytest.mark.asyncio
    async def test_best_rated_copy_is_promoted(self):
        items = [
            make_item(1, priority="none"),
            make_item(2, priority="medium", similar_to_id=1),
            make_item(3, priority="high", similar_to_id=1),
            make_item(4, priority="high", similar_to_id=1),
        ]
        classifier = FakeClassifier(
            {2: [match(1, 0.95)], 3: [match(1, 0.92)], 4: [match(3, 0.9)]}
        )

        plan = await plan_groups(items, classifier)

        # Highest priority wins; among equals the lowest id
        assert plan.new_top == {1: 3, 2: 3, 3: None, 4: 3}
        assert plan.promoted_groups == 1
        assert plan.link_info[1].method == METHOD_REBALANCED
        assert plan.link_info[1].score is None
        assert plan.link_info[2].method == "semantic"

        summary = summarize(items, plan)["summary"]
        assert summary["promoted_groups"] == 1
        assert summary["links_added"] == 1  # former top
        assert summary["links_removed"] == 1  # new top
        assert summary["links_repointed"] == 2
        assert summary["relevant_hidden_before"] == 3
        assert summary["relevant_hidden_after"] == 0

    @pytest.mark.asyncio
    async def test_equal_priority_keeps_the_earliest_item_on_top(self):
        items = [make_item(1, priority="medium"), make_item(2, priority="medium")]
        plan = await plan_groups(items, FakeClassifier({2: [match(1, 0.95)]}))

        assert plan.new_top == {1: None, 2: 1}
        assert plan.promoted_groups == 0

    @pytest.mark.asyncio
    async def test_exact_url_match_is_kept_without_asking_the_classifier(self):
        items = [
            make_item(1, url="https://www.example.com/story/?utm_source=rss", channel_id=1),
            make_item(2, url="https://example.com/story", channel_id=2, similar_to_id=1),
        ]
        classifier = FakeClassifier()

        plan = await plan_groups(items, classifier)

        assert plan.new_top == {1: None, 2: 1}
        assert plan.link_info[2].method == "url_match"
        assert [item_id for item_id, _ in classifier.calls] == [1]
        outcome = summarize(items, plan)
        assert outcome["changes"] == []
        assert outcome["summary"]["links_before"] == outcome["summary"]["links_after"] == 1

    @pytest.mark.asyncio
    async def test_same_url_on_the_same_channel_is_no_exact_match(self):
        items = [
            make_item(1, url="https://example.com/story"),
            make_item(2, url="https://example.com/story"),
        ]
        plan = await plan_groups(items, FakeClassifier())

        assert plan.new_top == {1: None, 2: None}

    @pytest.mark.asyncio
    async def test_content_hash_match_links(self):
        items = [make_item(1, content_hash="same"), make_item(2, content_hash="same")]
        plan = await plan_groups(items, FakeClassifier())

        assert plan.new_top == {1: None, 2: 1}
        assert plan.link_info[2].method == "content_hash"

    @pytest.mark.asyncio
    async def test_grey_band_links_only_with_similar_titles(self):
        title = "Vielen Kitas in Hessen fehlen weiterhin Fachkräfte"
        items = [
            make_item(1, title=title),
            make_item(2, title=f"Kinderbetreuung: {title}"),
            make_item(3, title="Landtag beschließt den Haushalt für 2027", similar_to_id=1),
        ]
        classifier = FakeClassifier(
            {2: [match(1, 0.80, title)], 3: [match(1, 0.80, title)]}
        )

        plan = await plan_groups(items, classifier)

        assert plan.new_top == {1: None, 2: 1, 3: None}
        assert plan.link_info[2].method == "semantic_title"

    @pytest.mark.asyncio
    async def test_not_indexed_item_loses_its_link(self):
        """A stored link is no evidence: without a vector the item is shown again."""
        items = [
            make_item(10),
            make_item(11, similar_to_id=10),  # target in range
            make_item(12, similar_to_id=5),  # target outside the range
            make_item(13),
        ]
        outside = {5: StoredLink(3, "none"), 3: StoredLink(None, "medium")}
        classifier = FakeClassifier({11: None, 12: None, 13: None})

        plan = await plan_groups(items, classifier, outside=outside)

        assert plan.new_top == {10: None, 11: None, 12: None, 13: None}
        assert plan.skipped_not_indexed == 3
        assert plan.unlinked_not_indexed == 2  # 13 had no link
        # All three go back to the dedup worker
        assert plan.recheck == {11, 12, 13}

        summary = summarize(items, plan, outside)["summary"]
        assert summary["links_removed"] == 2
        assert summary["skipped_not_indexed"] == 3
        assert summary["unlinked_not_indexed"] == 2
        assert summary["unverified_links_kept"] == 0

    @pytest.mark.asyncio
    async def test_not_indexed_item_with_an_exact_match_is_linked(self):
        """URL / hash rules come first and need no vector."""
        items = [make_item(1, content_hash="same"), make_item(2, content_hash="same")]
        classifier = FakeClassifier(default=None)

        plan = await plan_groups(items, classifier)

        assert plan.new_top == {1: None, 2: 1}
        assert [item_id for item_id, _ in classifier.calls] == [1]
        assert plan.skipped_not_indexed == 1
        assert plan.recheck == {1}

    @pytest.mark.asyncio
    async def test_not_indexed_items_are_skipped_without_limit(self):
        items = [make_item(1)] + [make_item(n, similar_to_id=1) for n in range(2, 41)]
        classifier = FakeClassifier(default=None)

        plan = await plan_groups(items, classifier, max_failed_streak=3)

        assert plan.skipped_not_indexed == 40
        assert plan.unlinked_not_indexed == 39
        assert all(top is None for top in plan.new_top.values())

    @pytest.mark.asyncio
    async def test_link_to_a_not_indexed_item_is_removed_and_rechecked(self):
        """The target has no vector, so it can never show up as a match."""
        items = [
            make_item(1),
            make_item(2, similar_to_id=3),  # indexed, compared, no match
            make_item(3),  # the stored top, not indexed
        ]
        plan = await plan_groups(items, FakeClassifier({3: None}))

        assert plan.new_top == {1: None, 2: None, 3: None}
        assert plan.skipped_not_indexed == 1
        assert plan.unlinked_not_indexed == 1
        assert plan.recheck == {2, 3}

    @pytest.mark.asyncio
    async def test_kept_link_never_closes_a_loop(self):
        items = [make_item(5, similar_to_id=9), make_item(9)]
        classifier = FakeClassifier({5: TimeoutError("slow"), 9: [match(5, 0.95)]})

        plan = await plan_groups(items, classifier)

        assert plan.new_top == {5: 9, 9: None}
        assert plan.link_info[5].method == METHOD_KEPT

    @pytest.mark.asyncio
    async def test_kept_link_follows_its_target_to_the_new_top(self):
        items = [
            make_item(1),
            make_item(2, similar_to_id=1),
            make_item(3, similar_to_id=2),
        ]
        classifier = FakeClassifier({2: [match(1, 0.95)], 3: TimeoutError("slow")})

        plan = await plan_groups(items, classifier)

        assert plan.new_top == {1: None, 2: 1, 3: 1}

    @pytest.mark.asyncio
    async def test_match_that_was_not_loaded_is_never_linked(self):
        items = [make_item(10), make_item(11)]
        classifier = FakeClassifier({11: [match(3, 0.99)]})

        plan = await plan_groups(items, classifier)

        assert plan.new_top == {10: None, 11: None}

    @pytest.mark.asyncio
    async def test_candidates_are_earlier_items_within_the_window(self):
        items = [
            make_item(1, fetched_at=T0),
            make_item(2, fetched_at=T0 + timedelta(days=2)),
            make_item(3, fetched_at=T0 + timedelta(days=4)),
            make_item(4, fetched_at=T0 + timedelta(days=4, hours=1)),
        ]
        classifier = FakeClassifier()

        await plan_groups(items, classifier, check_days=3)

        # Item 1 is asked too (without candidates): the answer tells whether
        # it is indexed at all
        assert classifier.calls == [(1, []), (2, [1]), (3, [2]), (4, [2, 3])]
        assert classifier.options[0] == {
            "threshold": DUPLICATE_THRESHOLD_MAYBE,
            "n_results": 20,
            "raise_on_error": True,
        }

    @pytest.mark.asyncio
    async def test_progress_callback_reports_the_total(self):
        seen = []
        items = [make_item(n) for n in range(1, 31)]

        await plan_groups(items, FakeClassifier(), progress_cb=lambda done, total: seen.append((done, total)))

        assert seen == [(25, 30), (30, 30)]

    @pytest.mark.asyncio
    async def test_stops_after_ten_failed_lookups_in_a_row(self):
        items = [make_item(n) for n in range(1, 31)]
        classifier = FakeClassifier(default=ConnectionError("classifier down"))

        with pytest.raises(LookupFailedError, match="10 lookups in a row failed"):
            await plan_groups(items, classifier)

        assert len(classifier.calls) == 10

    @pytest.mark.asyncio
    async def test_single_failed_lookups_keep_the_link_and_are_counted(self):
        items = [make_item(1)] + [make_item(n, similar_to_id=1) for n in range(2, 32)]
        # Every third lookup fails: never ten in a row
        answers = {n: TimeoutError("slow") for n in range(3, 32, 3)}
        classifier = FakeClassifier(answers, default=[match(1, 0.95)])

        plan = await plan_groups(items, classifier, max_failed_streak=2)

        assert plan.lookup_errors == 10
        assert plan.skipped_not_indexed == 0
        assert plan.new_top[3] == 1
        assert plan.link_info[3].method == METHOD_KEPT
        summary = summarize(items, plan)["summary"]
        assert summary["lookup_errors"] == 10
        assert summary["unverified_links_kept"] == 10
        assert summary["unlinked_not_indexed"] == 0
        assert summarize(items, plan)["changes"] == []

    @pytest.mark.asyncio
    async def test_failed_lookup_keeps_a_link_to_an_old_item_as_stored(self):
        """Kept through the stored top, without touching anything outside the range."""
        context = [make_item(5, similar_to_id=2)]
        outside = {2: StoredLink(None, "none")}
        items = [make_item(10, similar_to_id=5), make_item(11), make_item(12)]
        classifier = FakeClassifier({10: TimeoutError("slow"), 11: ConnectionError("down")})

        plan = await plan_groups(items, classifier, context=context, outside=outside)

        assert plan.new_top == {10: 2, 11: None, 12: None}
        assert plan.context_unlinked == {}
        assert classifier.calls == [(10, [5]), (11, [5, 10]), (12, [5, 10, 11])]
        # A top whose lookup failed goes back to the dedup worker
        assert plan.recheck == {11}

    @pytest.mark.asyncio
    async def test_short_range_with_only_failed_lookups_stops_too(self):
        items = [make_item(1), make_item(2, similar_to_id=1)]

        with pytest.raises(LookupFailedError, match="All 2 lookups failed"):
            await plan_groups(items, FakeClassifier(default=RuntimeError("boom")))

    # --- items fetched shortly before the range (read-only context) ---

    @pytest.mark.asyncio
    async def test_context_item_is_a_link_target_but_never_asked(self):
        context = [make_item(5), make_item(6, content_hash="same")]
        items = [make_item(10), make_item(11, content_hash="same")]
        classifier = FakeClassifier({10: [match(5, 0.95)]})

        plan = await plan_groups(items, classifier, context=context)

        assert plan.new_top == {10: 5, 11: 6}  # context items are not in the plan
        assert plan.link_info[10].method == "semantic"
        assert plan.link_info[11].method == "content_hash"
        assert classifier.calls == [(10, [5, 6])]
        assert plan.promoted_groups == 0
        assert plan.rebalance_tops == []

    @pytest.mark.asyncio
    async def test_context_copy_with_a_verified_link_leads_to_its_top(self):
        context = [make_item(5, similar_to_id=2)]
        outside = {2: StoredLink(None, "medium")}
        items = [make_item(10, similar_to_id=5), make_item(11, priority="low")]
        classifier = FakeClassifier(
            {5: [match(2, 0.93)], 10: [match(5, 0.9)], 11: [match(5, 0.9)]}
        )

        plan = await plan_groups(items, classifier, context=context, outside=outside)

        assert plan.new_top == {10: 2, 11: 2}
        assert plan.context_unlinked == {}
        assert plan.rebalance_tops == []  # the old top is rated better
        # The context link is checked once, when it is first needed
        assert classifier.calls == [(10, [5]), (5, [2]), (11, [5, 10])]
        summary = summarize(items, plan, {**outside, 5: StoredLink(2, "none")})["summary"]
        assert summary["links_repointed"] == 1
        assert summary["links_added"] == 1
        assert summary["context_unlinked"] == 0
        assert summary["relevant_hidden_after"] == 0

    @pytest.mark.asyncio
    async def test_context_copy_with_a_wrong_link_becomes_the_top(self):
        """Prod case: a Caritas item matched a context copy that was filed under
        "Lehrkräftebildungsgesetz" (cosine 0.24) and would have followed it there."""
        context = [
            make_item(5, title="Gemeinsam für Hessens Gemeinwohl", similar_to_id=2),
            make_item(6, similar_to_id=2),  # another wrong copy nobody matches
        ]
        outside = {2: StoredLink(None, "none")}  # "Lehrkräftebildungsgesetz"
        items = [
            make_item(10, title="Gemeinsam für Hessens Gemeinwohl - Caritas", priority="high"),
            make_item(11),
        ]
        # 5 vs 2 is far below the threshold: the lookup returns nothing
        classifier = FakeClassifier({5: [], 10: [match(5, 0.93)], 11: [match(5, 0.9)]})

        plan = await plan_groups(items, classifier, context=context, outside=outside)

        assert plan.new_top == {10: 5, 11: 5}  # under the item they match, not under 2
        assert plan.context_unlinked == {5: ContextUnlink(stored_link=2, recheck=False)}
        assert [call for call in classifier.calls if call[0] in (5, 6)] == [(5, [2])]
        # 10 (high) outranks the context item: promoted by the rebalance pass
        assert plan.rebalance_tops == [5]
        summary = summarize(items, plan, {**outside, 5: StoredLink(2, "none")})["summary"]
        assert summary["context_unlinked"] == 1
        assert summary["relevant_hidden_after"] == 0

    @pytest.mark.asyncio
    async def test_grey_band_context_link_needs_similar_titles(self):
        title = "Vielen Kitas in Hessen fehlen weiterhin Fachkräfte"
        context = [
            make_item(5, title=f"Kinderbetreuung: {title}", similar_to_id=2),
            make_item(6, title="Landtag beschließt den Haushalt für 2027", similar_to_id=2),
        ]
        outside = {2: StoredLink(None, "none")}
        items = [make_item(10, content_hash="hash-5"), make_item(11, content_hash="hash-6")]
        classifier = FakeClassifier({5: [match(2, 0.80, title)], 6: [match(2, 0.80, title)]})

        plan = await plan_groups(items, classifier, context=context, outside=outside)

        assert plan.new_top == {10: 2, 11: 6}
        assert plan.context_unlinked == {6: ContextUnlink(2, False)}

    @pytest.mark.asyncio
    async def test_context_copy_with_the_same_hash_as_its_top_needs_no_lookup(self):
        context = [make_item(5, content_hash="same", similar_to_id=2)]
        outside = {2: StoredLink(None, "none", False, None, "same", 1)}
        items = [make_item(10, content_hash="same")]
        classifier = FakeClassifier()

        plan = await plan_groups(items, classifier, context=context, outside=outside)

        assert plan.new_top == {10: 2}
        assert classifier.calls == []

    @pytest.mark.asyncio
    async def test_context_copy_without_a_vector_becomes_the_top_and_is_rechecked(self):
        context = [make_item(5, content_hash="same", similar_to_id=2)]
        outside = {2: StoredLink(None, "none")}
        items = [make_item(10, content_hash="same")]

        plan = await plan_groups(items, FakeClassifier({5: None}), context=context, outside=outside)

        assert plan.new_top == {10: 5}
        assert plan.context_unlinked == {5: ContextUnlink(stored_link=2, recheck=True)}

    @pytest.mark.asyncio
    async def test_promotion_over_a_top_outside_the_range_is_left_to_the_rebalance(self):
        context = [make_item(5)]
        items = [make_item(10, priority="high"), make_item(11, priority="medium")]
        classifier = FakeClassifier({10: [match(5, 0.95)], 11: [match(5, 0.95)]})

        plan = await plan_groups(items, classifier, context=context)

        # The context item is not re-linked by the plan ...
        assert plan.new_top == {10: 5, 11: 5}
        # ... but the promotion is counted and handed to the rebalance pass
        assert plan.promoted_groups == 1
        assert plan.rebalance_tops == [5]
        summary = summarize(items, plan, {5: StoredLink(None, "none")})["summary"]
        assert summary["promoted_groups"] == 1
        assert summary["relevant_hidden_after"] == 0

    @pytest.mark.asyncio
    async def test_archived_top_gives_way_to_a_visible_copy(self):
        items = [make_item(1, priority="medium", is_archived=True), make_item(2, priority="medium")]
        plan = await plan_groups(items, FakeClassifier({2: [match(1, 0.95)]}))

        assert plan.new_top == {1: 2, 2: None}
        assert plan.promoted_groups == 1


# =============================================================================
# Against the test database
# =============================================================================


@pytest_asyncio.fixture
async def maker(db_engine):
    session_maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    with patch("services.dedup_rebuild.async_session_maker", session_maker):
        yield session_maker


@pytest_asyncio.fixture
async def channels(maker) -> list[int]:
    async with maker() as db:
        source = Source(name="Rebuild Source", enabled=True)
        db.add(source)
        await db.flush()
        result = []
        for n in (1, 2):
            channel = Channel(
                source_id=source.id,
                name=f"Channel {n}",
                connector_type=ConnectorType.RSS,
                config={"url": f"https://feed{n}.example/rss"},
                source_identifier=f"https://feed{n}.example/rss",
                enabled=True,
            )
            db.add(channel)
            await db.flush()
            result.append(channel.id)
        await db.commit()
    return result


_counter = 0


async def add_item(maker, channel_id: int, title: str, **kwargs) -> int:
    global _counter
    _counter += 1
    values = {
        "channel_id": channel_id,
        "external_id": f"rebuild-{_counter}",
        "title": title,
        "content": f"content {_counter}",
        "url": f"https://example.com/rebuild/{_counter}",
        "published_at": utcnow(),
        "content_hash": f"rebuild-hash-{_counter}",
        "priority": Priority.NONE,
        "metadata_": {},
    }
    values.update(kwargs)
    async with maker() as db:
        item = Item(**values)
        db.add(item)
        await db.commit()
        return item.id


async def get_item(maker, item_id: int) -> Item:
    async with maker() as db:
        return (await db.execute(select(Item).where(Item.id == item_id))).scalar_one()


@pytest_asyncio.fixture
async def scenario(maker, channels):
    """Old item outside the range plus a broken chain and a hidden relevant copy.

    old (outside)  <- stray (in range, target outside)
    a(none) <- b(none) <- c(high)       c matches nothing
    d(none) <- e(high)                  e matches d
    """
    now = utcnow()
    old = await add_item(maker, channels[0], "Old", fetched_at=now - timedelta(days=30))
    recent = {"fetched_at": now - timedelta(hours=1)}
    stray = await add_item(
        maker, channels[0], "Stray", similar_to_id=old,
        metadata_={"duplicate_method": "semantic", "duplicate_score": 0.99, "keep": "me"},
        **recent,
    )
    a = await add_item(maker, channels[0], "A", **recent)
    b = await add_item(maker, channels[0], "B", similar_to_id=a, **recent)
    c = await add_item(
        maker, channels[0], "C", similar_to_id=b, priority=Priority.HIGH,
        metadata_={"duplicate_method": "semantic", "duplicate_score": 0.99},
        **recent,
    )
    d = await add_item(maker, channels[0], "D", **recent)
    e = await add_item(maker, channels[0], "E", similar_to_id=d, priority=Priority.HIGH, **recent)

    classifier = FakeClassifier({b: [match(a, 0.95)], e: [match(d, 0.91)]})
    return {
        "ids": {"old": old, "stray": stray, "a": a, "b": b, "c": c, "d": d, "e": e},
        "since": now - timedelta(days=1),
        "classifier": classifier,
    }


async def stored_links(maker, ids: dict) -> dict:
    return {name: (await get_item(maker, item_id)).similar_to_id for name, item_id in ids.items()}


async def set_fields(maker, item_id: int, **values) -> None:
    async with maker() as db:
        item = (await db.execute(select(Item).where(Item.id == item_id))).scalar_one()
        for name, value in values.items():
            setattr(item, name, value)
        await db.commit()


def interfere_after_planning(action):
    """Run ``action`` between planning and applying (another worker's write)."""
    original_plan = dedup_rebuild.plan_groups

    async def plan_then_interfere(*args, **kwargs):
        plan = await original_plan(*args, **kwargs)
        await action()
        return plan

    return patch("services.dedup_rebuild.plan_groups", plan_then_interfere)


class TestRebuildGroups:
    @pytest.mark.asyncio
    async def test_dry_run_reports_but_changes_nothing(self, maker, scenario):
        ids = scenario["ids"]
        before = await stored_links(maker, ids)

        summary = await rebuild_groups(scenario["since"], None, True, scenario["classifier"])

        assert summary["dry_run"] is True
        assert summary["items"] == 6  # "old" is outside the range
        assert summary["groups"] == 2
        assert summary["links_before"] == 4
        assert summary["links_after"] == 2
        assert summary["links_added"] == 1  # d under e
        assert summary["links_removed"] == 3  # stray, c, e
        assert summary["links_repointed"] == 0
        assert summary["promoted_groups"] == 1
        assert summary["skipped_not_indexed"] == 0
        assert summary["relevant_hidden_before"] == 2  # c and e
        assert summary["relevant_hidden_after"] == 0
        assert summary["unlinked_not_indexed"] == 0
        assert summary["lookup_errors"] == 0
        assert summary["unverified_links_kept"] == 0
        assert summary["context_items"] == 0
        assert summary["context_unlinked"] == 0
        assert summary["outside_links_to_copies"] == 0
        assert "applied" not in summary
        assert "rebalanced_after_apply" not in summary
        assert {sample["id"] for sample in summary["samples"]} == {
            ids["stray"], ids["c"], ids["d"], ids["e"],
        }
        sample_d = next(s for s in summary["samples"] if s["id"] == ids["d"])
        assert sample_d == {
            "id": ids["d"],
            "title": "D",
            "old_target": None,
            "new_target": ids["e"],
            "score": None,
            "method": METHOD_REBALANCED,
        }

        assert await stored_links(maker, ids) == before
        assert "dedup_rebuilt_at" not in (await get_item(maker, ids["c"])).metadata_

    @pytest.mark.asyncio
    async def test_real_run_applies_the_changes(self, maker, scenario):
        ids = scenario["ids"]

        summary = await rebuild_groups(scenario["since"], None, False, scenario["classifier"])

        assert summary["dry_run"] is False
        assert summary["applied"] == 4
        assert summary["apply_conflicts"] == 0
        assert summary["outside_repointed"] == 0
        assert summary["rebalanced_after_apply"] == 0
        assert summary["relevant_hidden_after"] == 0
        assert await stored_links(maker, ids) == {
            "old": None,
            "stray": None,  # target outside the range and no match in it
            "a": None,
            "b": ids["a"],
            "c": None,
            "d": ids["e"],
            "e": None,
        }

        # Unlinked: method/score removed, other metadata kept
        stray = (await get_item(maker, ids["stray"])).metadata_
        assert "duplicate_method" not in stray and "duplicate_score" not in stray
        assert stray["keep"] == "me"
        assert "dedup_rebuilt_at" in stray
        c = (await get_item(maker, ids["c"])).metadata_
        assert "duplicate_method" not in c and "duplicate_score" not in c

        # New link
        d = (await get_item(maker, ids["d"])).metadata_
        assert d["duplicate_method"] == METHOD_REBALANCED
        assert "duplicate_score" not in d
        assert "dedup_rebuilt_at" in d

        # Unchanged rows and items outside the range are not written
        assert "dedup_rebuilt_at" not in (await get_item(maker, ids["b"])).metadata_
        assert (await get_item(maker, ids["old"])).metadata_ == {}

        # A second run finds nothing left to do
        again = await rebuild_groups(
            scenario["since"], None, False,
            FakeClassifier({ids["b"]: [match(ids["a"], 0.95)], ids["e"]: [match(ids["d"], 0.91)]}),
        )
        assert again["links_added"] == again["links_removed"] == again["links_repointed"] == 0
        assert again["applied"] == 0

    @pytest.mark.asyncio
    async def test_semantic_link_stores_method_and_score(self, maker, channels):
        now = utcnow()
        first = await add_item(maker, channels[0], "First", fetched_at=now)
        second = await add_item(maker, channels[1], "Second", fetched_at=now)

        summary = await rebuild_groups(
            now - timedelta(hours=1), None, False,
            FakeClassifier({second: [match(first, 0.93)]}),
        )

        assert summary["links_added"] == 1
        item = await get_item(maker, second)
        assert item.similar_to_id == first
        assert item.metadata_["duplicate_method"] == "semantic"
        assert item.metadata_["duplicate_score"] == pytest.approx(0.93)

    @pytest.mark.asyncio
    async def test_until_limits_the_range(self, maker, channels):
        now = utcnow()
        early = await add_item(maker, channels[0], "Early", fetched_at=now - timedelta(days=2))
        late = await add_item(
            maker, channels[1], "Late", fetched_at=now, url="https://example.com/same",
            similar_to_id=early,
        )

        summary = await rebuild_groups(
            now - timedelta(days=3), now - timedelta(days=1), False, FakeClassifier()
        )

        assert summary["items"] == 1
        assert (await get_item(maker, late)).similar_to_id == early  # untouched

    @pytest.mark.asyncio
    async def test_row_changed_during_the_run_is_left_alone(self, maker, scenario):
        """Optimistic apply: a link changed after loading is counted, not overwritten."""
        ids = scenario["ids"]

        async def relink_c():
            await set_fields(maker, ids["c"], similar_to_id=ids["e"])

        with interfere_after_planning(relink_c):
            summary = await rebuild_groups(scenario["since"], None, False, scenario["classifier"])

        assert summary["applied"] == 3
        assert summary["apply_conflicts"] == 1
        assert (await get_item(maker, ids["c"])).similar_to_id == ids["e"]

    # --- apply: live priorities, never a hidden group ---

    @pytest.mark.asyncio
    async def test_priority_changed_while_planning_is_rebalanced(self, maker, scenario):
        """The plan puts e (high) over d (none); meanwhile the verdicts flip."""
        ids = scenario["ids"]

        async def flip_priorities():
            await set_fields(maker, ids["d"], priority=Priority.HIGH)
            await set_fields(maker, ids["e"], priority=Priority.NONE)

        with interfere_after_planning(flip_priorities):
            summary = await rebuild_groups(scenario["since"], None, False, scenario["classifier"])

        links = await stored_links(maker, ids)
        assert links["d"] is None  # the relevant item is the visible one
        assert links["e"] == ids["d"]
        assert summary["rebalanced_after_apply"] == 1
        assert summary["relevant_hidden_after"] == 0

    @pytest.mark.asyncio
    async def test_priority_change_in_an_unchanged_group_is_rebalanced_too(self, maker, scenario):
        ids = scenario["ids"]

        async def raise_b():
            await set_fields(maker, ids["b"], priority=Priority.MEDIUM)

        with interfere_after_planning(raise_b):
            summary = await rebuild_groups(scenario["since"], None, False, scenario["classifier"])

        links = await stored_links(maker, ids)
        assert links["b"] is None
        assert links["a"] == ids["b"]
        assert summary["rebalanced_after_apply"] == 1

    @pytest.mark.asyncio
    async def test_interrupted_top_swap_shows_both_items(self, maker, scenario):
        """d <- e becomes e <- d. Dying between the passes must not hide the group."""
        ids = scenario["ids"]
        seen = {}

        async def die(*args, **kwargs):
            seen.update(await stored_links(maker, ids))  # committed state at that point
            raise RuntimeError("process died")

        with patch("services.dedup_rebuild._link_copies", die):
            with pytest.raises(RuntimeError, match="process died"):
                await rebuild_groups(scenario["since"], None, False, scenario["classifier"])

        links = await stored_links(maker, ids)
        assert links == seen
        assert links["d"] is None and links["e"] is None  # too many visible, none hidden
        assert links["c"] is None
        assert links["b"] == ids["a"]

    @pytest.mark.asyncio
    async def test_copy_is_not_linked_under_an_item_that_is_no_longer_a_top(self, maker, scenario):
        """e was meant to become the top of d but got linked elsewhere meanwhile."""
        ids = scenario["ids"]

        async def relink_e():
            await set_fields(maker, ids["e"], similar_to_id=ids["a"])

        with interfere_after_planning(relink_e):
            summary = await rebuild_groups(scenario["since"], None, False, scenario["classifier"])

        links = await stored_links(maker, ids)
        assert summary["apply_conflicts"] == 2  # e (changed) and d (target is no top)
        assert links["d"] is None  # stays visible instead of hanging under a copy
        # e (high) sat under a (none): the rebalance pass put it on top
        assert links["e"] is None
        assert links["a"] == ids["e"] and links["b"] == ids["e"]
        assert summary["rebalanced_after_apply"] == 1

    # --- never unlink without evidence / range boundary ---

    @pytest.mark.asyncio
    async def test_not_indexed_item_is_unlinked_and_handed_back_to_the_worker(
        self, maker, scenario
    ):
        ids = scenario["ids"]
        classifier = scenario["classifier"]
        done = {"dedup_phase1": True, "dedup_phase2": True, "dedup_phase2_at": "2026-11-01T00:00:00"}
        await set_fields(maker, ids["stray"], metadata_={
            **done, "duplicate_method": "semantic", "duplicate_score": 0.99, "keep": "me",
        })
        await set_fields(maker, ids["a"], metadata_=dict(done))  # no link, no vector
        await set_fields(maker, ids["b"], metadata_=dict(done))  # indexed, link confirmed
        classifier.answers[ids["stray"]] = None
        classifier.answers[ids["a"]] = None

        summary = await rebuild_groups(scenario["since"], None, False, classifier)

        assert summary["skipped_not_indexed"] == 2
        assert summary["unlinked_not_indexed"] == 1
        assert summary["links_removed"] == 3  # stray, c and e
        assert summary["unverified_links_kept"] == 0

        stray = await get_item(maker, ids["stray"])
        assert stray.similar_to_id is None  # visible beats hidden
        assert stray.metadata_ == {
            "dedup_phase1": True, "keep": "me",
            "dedup_rebuilt_at": stray.metadata_["dedup_rebuilt_at"],
        }
        # No link before or after: only the worker's done-marker goes
        assert (await get_item(maker, ids["a"])).metadata_ == {"dedup_phase1": True}
        assert (await get_item(maker, ids["b"])).metadata_ == done

    @pytest.mark.asyncio
    async def test_many_not_indexed_items_do_not_stop_the_run(self, maker, channels):
        now = utcnow()
        top = await add_item(maker, channels[0], "Top", fetched_at=now)
        copies = [
            await add_item(maker, channels[0], f"Copy {n}", similar_to_id=top, fetched_at=now)
            for n in range(14)
        ]

        summary = await rebuild_groups(
            now - timedelta(hours=1), None, False, FakeClassifier(default=None)
        )

        assert summary["skipped_not_indexed"] == 15
        assert summary["unlinked_not_indexed"] == 14
        assert summary["links_removed"] == 14
        assert summary["applied"] == 14
        assert {(await get_item(maker, copy)).similar_to_id for copy in copies} == {None}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dry_run", [True, False])
    async def test_dead_classifier_stops_the_run_before_writing(self, maker, channels, dry_run):
        now = utcnow()
        top = await add_item(maker, channels[0], "Top", fetched_at=now)
        copies = [
            await add_item(maker, channels[0], f"Copy {n}", similar_to_id=top, fetched_at=now)
            for n in range(14)
        ]
        classifier = FakeClassifier(default=ConnectionError("classifier down"))

        with pytest.raises(LookupFailedError):
            await rebuild_groups(now - timedelta(hours=1), None, dry_run, classifier)

        assert len(classifier.calls) == 10
        assert {(await get_item(maker, copy)).similar_to_id for copy in copies} == {top}

    @pytest.mark.asyncio
    async def test_item_before_the_range_leads_to_its_old_top_if_that_link_holds(
        self, maker, channels
    ):
        now = utcnow()
        since = now - timedelta(days=1)
        old_top = await add_item(
            maker, channels[0], "Old top", priority=Priority.MEDIUM,
            fetched_at=now - timedelta(days=30),
        )
        before = await add_item(
            maker, channels[0], "Before", url="https://example.com/shared",
            similar_to_id=old_top, fetched_at=now - timedelta(days=2),
            metadata_={"duplicate_method": "semantic", "duplicate_score": 0.9},
        )
        by_url = await add_item(
            maker, channels[1], "By URL", url="https://example.com/shared", fetched_at=now
        )
        by_vector = await add_item(maker, channels[1], "By vector", fetched_at=now)
        classifier = FakeClassifier(
            {before: [match(old_top, 0.92)], by_vector: [match(before, 0.95)]}
        )

        summary = await rebuild_groups(since, None, False, classifier)

        assert summary["items"] == 2
        assert summary["context_items"] == 1
        assert summary["context_unlinked"] == 0
        assert summary["links_added"] == 2
        assert summary["promoted_groups"] == 0
        assert (await get_item(maker, by_url)).similar_to_id == old_top
        assert (await get_item(maker, by_vector)).similar_to_id == old_top
        assert (await get_item(maker, by_url)).metadata_["duplicate_method"] == "url_match"
        # The context item's own link was checked once, before it was first used
        assert classifier.calls == [(before, [old_top]), (by_vector, [before, by_url])]
        context_item = await get_item(maker, before)
        assert context_item.similar_to_id == old_top
        assert context_item.metadata_ == {"duplicate_method": "semantic", "duplicate_score": 0.9}

    @pytest.mark.asyncio
    async def test_item_is_not_filed_under_the_wrong_top_of_a_context_copy(self, maker, channels):
        """Prod case 128514: matched a context copy whose own link was wrong (0.24)."""
        now = utcnow()
        since = now - timedelta(days=1)
        unrelated = await add_item(
            maker, channels[0], "Lehrkräftebildungsgesetz", fetched_at=now - timedelta(days=30)
        )
        context_copy = await add_item(
            maker, channels[0], "Gemeinsam für Hessens Gemeinwohl",
            similar_to_id=unrelated, fetched_at=now - timedelta(days=2),
            metadata_={
                "duplicate_method": "semantic", "duplicate_score": 0.99,
                "dedup_phase2": True, "keep": "me",
            },
        )
        caritas = await add_item(
            maker, channels[1], "Gemeinsam für Hessens Gemeinwohl - Caritas",
            priority=Priority.HIGH, fetched_at=now,
        )
        # context_copy vs unrelated is below the threshold: the lookup returns nothing
        answers = {context_copy: [], caritas: [match(context_copy, 0.93)]}

        dry = await rebuild_groups(since, None, True, FakeClassifier(answers))

        assert dry["context_unlinked"] == 1
        assert dry["links_added"] == 1
        assert dry["promoted_groups"] == 1
        assert dry["relevant_hidden_after"] == 0
        assert (await get_item(maker, context_copy)).similar_to_id == unrelated  # nothing written
        assert (await get_item(maker, caritas)).similar_to_id is None

        real = await rebuild_groups(since, None, False, FakeClassifier(answers))

        assert real["context_unlinked"] == 1
        assert real["apply_conflicts"] == 0
        assert real["rebalanced_after_apply"] == 1
        assert real["relevant_hidden_after"] == 0
        # The two belong together and the relevant one is on top; nothing of
        # it hangs under the unrelated old item any more
        assert (await get_item(maker, caritas)).similar_to_id is None
        former_copy = await get_item(maker, context_copy)
        assert former_copy.similar_to_id == caritas
        assert former_copy.metadata_.get("duplicate_score") is None
        assert former_copy.metadata_["dedup_phase2"] is True  # it was compared
        assert former_copy.metadata_["keep"] == "me"
        async with maker() as db:
            under_unrelated = (
                await db.execute(select(Item.id).where(Item.similar_to_id == unrelated))
            ).all()
        assert under_unrelated == []

    @pytest.mark.asyncio
    async def test_context_copy_changed_meanwhile_is_left_alone(self, maker, channels):
        now = utcnow()
        since = now - timedelta(days=1)
        unrelated = await add_item(maker, channels[0], "Unrelated", fetched_at=now - timedelta(days=30))
        other = await add_item(maker, channels[0], "Other", fetched_at=now - timedelta(days=30))
        context_copy = await add_item(
            maker, channels[0], "Story", similar_to_id=unrelated, fetched_at=now - timedelta(days=2)
        )
        new = await add_item(maker, channels[1], "Story again", fetched_at=now)
        classifier = FakeClassifier({context_copy: [], new: [match(context_copy, 0.93)]})

        async def relink_context():
            await set_fields(maker, context_copy, similar_to_id=other)

        with interfere_after_planning(relink_context):
            summary = await rebuild_groups(since, None, False, classifier)

        assert summary["context_unlinked"] == 0
        assert summary["apply_conflicts"] == 2  # the context item and its would-be copy
        assert (await get_item(maker, context_copy)).similar_to_id == other
        assert (await get_item(maker, new)).similar_to_id is None  # visible, not under a copy

    @pytest.mark.asyncio
    async def test_relevant_item_under_a_top_before_the_range(self, maker, channels):
        """The plan links it; the rebalance pass of a real run promotes it."""
        now = utcnow()
        since = now - timedelta(days=1)
        before = await add_item(maker, channels[0], "Before", fetched_at=now - timedelta(days=2))
        relevant = await add_item(
            maker, channels[1], "Relevant", priority=Priority.HIGH, fetched_at=now
        )
        answers = {relevant: [match(before, 0.95)]}

        dry = await rebuild_groups(since, None, True, FakeClassifier(answers))

        assert dry["promoted_groups"] == 1  # planned promotion
        assert dry["links_added"] == 1
        assert dry["relevant_hidden_after"] == 0
        assert (await get_item(maker, relevant)).similar_to_id is None
        assert (await get_item(maker, before)).similar_to_id is None

        real = await rebuild_groups(since, None, False, FakeClassifier(answers))

        assert real["promoted_groups"] == 1
        assert real["rebalanced_after_apply"] == 1
        assert real["relevant_hidden_after"] == 0
        assert (await get_item(maker, relevant)).similar_to_id is None
        assert (await get_item(maker, before)).similar_to_id == relevant

    @pytest.mark.asyncio
    async def test_row_outside_the_range_follows_its_target_to_the_new_top(self, maker, channels):
        now = utcnow()
        since = now - timedelta(days=1)
        old = await add_item(maker, channels[0], "Old copy", fetched_at=now - timedelta(days=30))
        first = await add_item(maker, channels[0], "First", fetched_at=now)
        second = await add_item(maker, channels[1], "Second", fetched_at=now)
        await set_fields(maker, old, similar_to_id=second)  # second was promoted earlier
        answers = {second: [match(first, 0.95)]}

        dry = await rebuild_groups(since, None, True, FakeClassifier(answers))

        assert dry["outside_links_to_copies"] == 1
        assert "outside_repointed" not in dry
        assert (await get_item(maker, old)).similar_to_id == second

        real = await rebuild_groups(since, None, False, FakeClassifier(answers))

        assert real["outside_links_to_copies"] == 1
        assert real["outside_repointed"] == 1
        assert (await get_item(maker, second)).similar_to_id == first
        old_item = await get_item(maker, old)
        assert old_item.similar_to_id == first  # no copy -> copy chain left behind
        assert old_item.metadata_ == {}


# =============================================================================
# Endpoint
# =============================================================================


@pytest_asyncio.fixture
async def rebuild_env(maker):
    """Fresh run state, in-memory status store, dedup worker stopped."""
    store: dict = {}

    async def write_stats(name, stats):
        store[name] = {**stats, "synced_at": utcnow().isoformat()}

    async def read_stats(name):
        return dict(store.get(name) or {})

    dedup_rebuild._status = dedup_rebuild._idle_status()
    dedup_rebuild._task = None
    worker_state = {"running": False, "paused": False}

    with (
        patch("services.worker_status.write_stats", write_stats),
        patch("services.worker_status.read_stats", read_stats),
        patch("services.dedup_worker.get_dedup_worker", return_value=None),
        patch("api.admin.dedup.read_state", AsyncMock(side_effect=lambda name: dict(worker_state))),
    ):
        yield {"store": store, "worker_state": worker_state}

    task = dedup_rebuild._task
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    dedup_rebuild._status = dedup_rebuild._idle_status()
    dedup_rebuild._task = None


@asynccontextmanager
async def run_lock_held_elsewhere(engine):
    """Hold the run lock on another connection, like a second worker process."""
    async with engine.connect() as conn:
        acquired = (await conn.execute(select(func.pg_try_advisory_lock(RUN_LOCK_KEY)))).scalar()
        try:
            yield acquired
        finally:
            if acquired:
                await conn.execute(select(func.pg_advisory_unlock(RUN_LOCK_KEY)))


def use_classifier(classifier):
    return patch(
        "services.relevance_filter.create_relevance_filter",
        new_callable=AsyncMock,
        return_value=classifier,
    )


SINCE = {"since": "2020-01-01T00:00:00"}


class TestRebuildEndpoint:
    @pytest.mark.asyncio
    async def test_status_is_idle_before_any_run(self, client, rebuild_env):
        response = await client.get("/api/admin/dedup/rebuild")

        assert response.status_code == 200
        assert response.json() == {
            "state": "idle",
            "dry_run": None,
            "since": None,
            "until": None,
            "started_at": None,
            "finished_at": None,
            "progress": {"processed": 0, "total": 0},
            "result": None,
            "error": None,
        }

    @pytest.mark.asyncio
    async def test_dry_run_is_the_default_and_runs_in_the_background(
        self, client, rebuild_env, scenario
    ):
        ids = scenario["ids"]
        before = await stored_links(maker=dedup_rebuild.async_session_maker, ids=ids)
        classifier = scenario["classifier"]

        with use_classifier(classifier):
            response = await client.post("/api/admin/dedup/rebuild", json=SINCE)

        assert response.status_code == 202
        body = response.json()
        assert body["state"] == "running"
        assert body["dry_run"] is True
        assert body["since"] == "2020-01-01T00:00:00"
        assert body["result"] is None

        await dedup_rebuild._task

        status = (await client.get("/api/admin/dedup/rebuild")).json()
        assert status["state"] == "done"
        assert status["error"] is None
        assert status["finished_at"] is not None
        assert status["progress"] == {"processed": 7, "total": 7}
        assert status["result"]["items"] == 7
        assert status["result"]["dry_run"] is True
        assert status["result"]["links_removed"] == 3  # stray, c and e
        assert classifier.closed is True
        assert await stored_links(maker=dedup_rebuild.async_session_maker, ids=ids) == before
        # Mirrored for the other worker process
        assert rebuild_env["store"]["dedup_rebuild"]["state"] == "done"

    @pytest.mark.asyncio
    async def test_real_run_applies_when_the_dedup_worker_is_paused(
        self, client, rebuild_env, scenario
    ):
        ids = scenario["ids"]
        rebuild_env["worker_state"].update(running=True, paused=True)

        with use_classifier(scenario["classifier"]):
            response = await client.post(
                "/api/admin/dedup/rebuild",
                json={"since": scenario["since"].isoformat(), "dry_run": False},
            )
        assert response.status_code == 202

        await dedup_rebuild._task

        status = (await client.get("/api/admin/dedup/rebuild")).json()
        assert status["state"] == "done"
        assert status["result"]["applied"] == 4
        links = await stored_links(maker=dedup_rebuild.async_session_maker, ids=ids)
        assert links["c"] is None
        assert links["d"] == ids["e"]

    @pytest.mark.asyncio
    async def test_real_run_is_refused_while_the_dedup_worker_runs(self, client, rebuild_env):
        rebuild_env["worker_state"].update(running=True, paused=False)
        factory = AsyncMock(return_value=FakeClassifier())

        with patch("services.relevance_filter.create_relevance_filter", factory):
            response = await client.post(
                "/api/admin/dedup/rebuild", json={**SINCE, "dry_run": False}
            )

        assert response.status_code == 409
        assert "pause" in response.json()["detail"].lower()
        factory.assert_not_called()
        assert dedup_rebuild._task is None

    @pytest.mark.asyncio
    async def test_real_run_is_refused_while_the_local_dedup_worker_runs(
        self, client, rebuild_env
    ):
        worker = MagicMock(_running=True, _paused=False)
        with patch("services.dedup_worker.get_dedup_worker", return_value=worker):
            response = await client.post(
                "/api/admin/dedup/rebuild", json={**SINCE, "dry_run": False}
            )
            assert response.status_code == 409

            worker._paused = True
            with use_classifier(FakeClassifier()):
                response = await client.post(
                    "/api/admin/dedup/rebuild", json={**SINCE, "dry_run": False}
                )
            assert response.status_code == 202
            await dedup_rebuild._task

    @pytest.mark.asyncio
    async def test_dry_run_is_allowed_while_the_dedup_worker_runs(self, client, rebuild_env):
        rebuild_env["worker_state"].update(running=True, paused=False)

        with use_classifier(FakeClassifier()):
            response = await client.post("/api/admin/dedup/rebuild", json=SINCE)

        assert response.status_code == 202
        await dedup_rebuild._task

    @pytest.mark.asyncio
    async def test_second_start_is_refused_while_a_run_is_active(
        self, client, rebuild_env, scenario
    ):
        release = asyncio.Event()

        class BlockingClassifier(FakeClassifier):
            async def find_duplicates_by_id(self, *args, **kwargs):
                await release.wait()
                return []

        with use_classifier(BlockingClassifier()):
            first = await client.post("/api/admin/dedup/rebuild", json=SINCE)
        assert first.status_code == 202

        second_classifier = FakeClassifier()
        with use_classifier(second_classifier):
            second = await client.post("/api/admin/dedup/rebuild", json=SINCE)
        assert second.status_code == 409
        assert "already running" in second.json()["detail"]

        status = (await client.get("/api/admin/dedup/rebuild")).json()
        assert status["state"] == "running"

        release.set()
        await dedup_rebuild._task
        assert (await client.get("/api/admin/dedup/rebuild")).json()["state"] == "done"

    @pytest.mark.asyncio
    async def test_run_of_another_worker_process_blocks_and_is_reported(
        self, client, rebuild_env
    ):
        rebuild_env["store"]["dedup_rebuild"] = {
            **dedup_rebuild._idle_status(),
            "state": "running",
            "dry_run": False,
            "started_at": utcnow().isoformat(),
            "progress": {"processed": 40, "total": 100},
            "synced_at": utcnow().isoformat(),
        }

        status = (await client.get("/api/admin/dedup/rebuild")).json()
        assert status["state"] == "running"
        assert status["progress"] == {"processed": 40, "total": 100}

        with use_classifier(FakeClassifier()):
            response = await client.post("/api/admin/dedup/rebuild", json=SINCE)
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_abandoned_run_is_reported_as_failed(self, client, rebuild_env):
        long_ago = (utcnow() - timedelta(hours=2)).isoformat()
        rebuild_env["store"]["dedup_rebuild"] = {
            **dedup_rebuild._idle_status(),
            "state": "running",
            "started_at": long_ago,
            "synced_at": long_ago,
        }

        with patch("services.worker_status.read_stats", AsyncMock(
            return_value=dict(rebuild_env["store"]["dedup_rebuild"])
        )):
            status = (await client.get("/api/admin/dedup/rebuild")).json()

        assert status["state"] == "failed"
        assert "interrupted" in status["error"]

    @pytest.mark.asyncio
    async def test_failure_is_reported(self, client, rebuild_env, scenario):
        class BrokenClassifier(FakeClassifier):
            async def find_duplicates_by_id(self, *args, **kwargs):
                raise RuntimeError("boom")

        classifier = BrokenClassifier()
        with use_classifier(classifier):
            response = await client.post("/api/admin/dedup/rebuild", json=SINCE)
        assert response.status_code == 202

        await dedup_rebuild._task

        status = (await client.get("/api/admin/dedup/rebuild")).json()
        assert status["state"] == "failed"
        assert status["error"].startswith("LookupFailedError: All 7 lookups failed")
        assert status["result"] is None
        assert classifier.closed is True

    @pytest.mark.asyncio
    async def test_start_is_refused_while_another_process_holds_the_run_lock(
        self, client, rebuild_env, db_engine
    ):
        """Nothing in the status store yet: only the database lock tells."""
        refused = FakeClassifier()
        async with run_lock_held_elsewhere(db_engine) as acquired:
            assert acquired is True
            with use_classifier(refused):
                response = await client.post("/api/admin/dedup/rebuild", json=SINCE)

            assert response.status_code == 409
            assert "already running" in response.json()["detail"]
            assert refused.closed is True
            assert dedup_rebuild._task is None

        with use_classifier(FakeClassifier()):
            response = await client.post("/api/admin/dedup/rebuild", json=SINCE)
        assert response.status_code == 202
        await dedup_rebuild._task

    @pytest.mark.asyncio
    async def test_simultaneous_starts_yield_exactly_one_run(self, rebuild_env, db_engine):
        release = asyncio.Event()

        class BlockingClassifier(FakeClassifier):
            async def find_duplicates_by_id(self, *args, **kwargs):
                await release.wait()
                return []

        since = datetime(2020, 1, 1)
        outcomes = await asyncio.gather(
            dedup_rebuild.start_rebuild(since, None, True, BlockingClassifier()),
            dedup_rebuild.start_rebuild(since, None, True, BlockingClassifier()),
            return_exceptions=True,
        )

        assert sum(isinstance(o, RebuildAlreadyRunning) for o in outcomes) == 1
        assert sum(isinstance(o, dict) and o["state"] == "running" for o in outcomes) == 1
        # The run holds the lock for its whole duration ...
        async with run_lock_held_elsewhere(db_engine) as acquired:
            assert acquired is False

        release.set()
        await dedup_rebuild._task

        # ... and gives it back afterwards
        async with run_lock_held_elsewhere(db_engine) as acquired:
            assert acquired is True

    @pytest.mark.asyncio
    async def test_run_lock_is_released_after_a_failed_run(self, client, rebuild_env, db_engine):
        with use_classifier(FakeClassifier()), patch(
            "services.dedup_rebuild.rebuild_groups", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            response = await client.post("/api/admin/dedup/rebuild", json=SINCE)
            assert response.status_code == 202
            await dedup_rebuild._task

        status = (await client.get("/api/admin/dedup/rebuild")).json()
        assert status["state"] == "failed"
        assert status["error"] == "RuntimeError: boom"
        async with run_lock_held_elsewhere(db_engine) as acquired:
            assert acquired is True

    @pytest.mark.asyncio
    async def test_classifier_unavailable(self, client, rebuild_env):
        with use_classifier(None):
            response = await client.post("/api/admin/dedup/rebuild", json=SINCE)

        assert response.status_code == 503
        assert (await client.get("/api/admin/dedup/rebuild")).json()["state"] == "idle"

    @pytest.mark.asyncio
    async def test_invalid_range_and_missing_since(self, client, rebuild_env):
        response = await client.post(
            "/api/admin/dedup/rebuild",
            json={"since": "2026-11-02T00:00:00", "until": "2026-11-01T00:00:00"},
        )
        assert response.status_code == 422

        response = await client.post("/api/admin/dedup/rebuild", json={})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_timezone_aware_since_is_converted_to_utc(self, client, rebuild_env):
        with use_classifier(FakeClassifier()):
            response = await client.post(
                "/api/admin/dedup/rebuild", json={"since": "2026-11-01T02:00:00+02:00"}
            )

        assert response.status_code == 202
        assert response.json()["since"] == "2026-11-01T00:00:00"
        await dedup_rebuild._task
