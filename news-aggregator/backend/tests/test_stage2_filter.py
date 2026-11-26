"""Stage-2 junk filter: client fail-open behaviour and the LLM-worker skip path.

The worker tests run against the real test database (``db_engine``)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from services.llm_worker import LLMWorker
from services.stage2_filter import SKIP_REASON, Stage2Filter, Stage2Result


def _transport(handler):
    return httpx.MockTransport(handler)


async def _check_with(handler, pre_filter=None):
    f = Stage2Filter("http://stage2.test", timeout=1.0)
    f._client = httpx.AsyncClient(transport=_transport(handler))
    try:
        return await f.check("Titel", "Inhalt", pre_filter)
    finally:
        await f.close()


class TestStage2Client:
    @pytest.mark.asyncio
    async def test_disabled_returns_none(self):
        assert await Stage2Filter("", 1.0).check("t", "c", None) is None

    @pytest.mark.asyncio
    async def test_skip_and_payload(self):
        seen = {}

        def handler(request):
            import json

            seen.update(json.loads(request.content))
            return httpx.Response(200, json={
                "keep": False, "mode": "ensemble", "score": -1.2, "threshold": 0.75,
                "student_score": -6.0, "model": "gbert512_s0",
            })

        res = await _check_with(handler, {"relevance_raw_score": -5.0, "gate_model": "m1-x"})
        assert res.keep is False and res.decision == "skip" and res.mode == "ensemble"
        assert seen["m1_score"] == -5.0 and seen["m1_model"] == "m1-x"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("handler", [
        lambda r: httpx.Response(500, text="boom"),
        lambda r: httpx.Response(200, json={"mode": "student"}),  # no keep
        lambda r: httpx.Response(200, json={"keep": "no"}),        # not a bool
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")),
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
    ])
    async def test_fail_open(self, handler):
        res = await _check_with(handler)
        assert res.keep is True and res.decision == "fail_open" and res.error


class TestStage2Worker:
    @pytest.fixture
    def worker(self):
        return LLMWorker(batch_size=5, idle_sleep=1.0, backlog_batch_size=10)

    @pytest.fixture
    def session_maker(self, db_engine):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        with patch("services.llm_worker.async_session_maker", new=maker):
            yield maker

    @staticmethod
    async def _seed_item(maker) -> int:
        from database import utcnow
        from models import Channel, ConnectorType, Item, Priority, Source

        async with maker() as db:
            source = Source(name="Stage2")
            db.add(source)
            await db.flush()
            channel = Channel(source_id=source.id, connector_type=ConnectorType.RSS, config={})
            db.add(channel)
            await db.flush()
            item = Item(
                channel_id=channel.id, external_id="s2", title="Titel", content="Inhalt",
                url="https://example.com/s2", published_at=utcnow(), content_hash="hash-s2",
                priority=Priority.NONE, priority_score=50, needs_llm_processing=True,
                metadata_={"pre_filter": {"relevance_confidence": 0.3,
                                          "relevance_raw_score": -3.0, "gate_model": "m1-x"}},
            )
            db.add(item)
            await db.commit()
            return item.id

    @staticmethod
    def _processor() -> MagicMock:
        analysis = {"summary": "Zusammenfassung", "priority": "medium", "relevant": True,
                    "relevance_score": 0.9, "assigned_aks": [], "tags": []}
        p = MagicMock()
        p.analyze_from_data_with_messages = AsyncMock(
            return_value=(analysis, [{"role": "assistant", "content": "{}"}]))
        p.extract_topics = AsyncMock(return_value=("Sonstiges", None))
        p.confirm_duplicate = AsyncMock(return_value=(False, ""))
        return p

    @staticmethod
    def _stage2(result):
        f = MagicMock()
        f.check = AsyncMock(return_value=result)
        return patch("services.llm_worker.get_stage2_filter", return_value=f)

    @pytest.mark.asyncio
    async def test_skip_bypasses_llm_and_is_logged(self, worker, session_maker):
        from models import Item, ItemEvent, ItemProcessingLog, Priority

        item_id = await self._seed_item(session_maker)
        proc = self._processor()
        skip = Stage2Result(keep=False, decision="skip", mode="ensemble", score=-1.0,
                            threshold=0.75, student_score=-6.0, model="gbert512_s0")
        with self._stage2(skip) as get_filter:
            assert await worker._process_items([item_id], proc, is_fresh=True) == 1

        f = get_filter.return_value
        assert f.check.await_args.args[2]["relevance_raw_score"] == -3.0
        proc.analyze_from_data_with_messages.assert_not_awaited()
        assert worker._stats["stage2_skipped"] == 1
        async with session_maker() as db:
            item = await db.get(Item, item_id)
            assert item.priority == Priority.NONE
            assert item.needs_llm_processing is False
            assert item.priority_score == 20
            meta = item.metadata_["stage2_filter"]
            assert meta["decision"] == "skip" and meta["skip_reason"] == SKIP_REASON
            assert meta["score"] == -1.0 and meta["threshold"] == 0.75
            logs = (await db.execute(select(ItemProcessingLog).where(
                ItemProcessingLog.item_id == item_id))).scalars().all()
            assert [(l.step_type, l.skipped, l.skip_reason) for l in logs] == [
                ("stage2_filter", True, SKIP_REASON)]
            events = (await db.execute(select(ItemEvent.event_type).where(
                ItemEvent.item_id == item_id))).scalars().all()
            assert "stage2_filtered" in events

    @pytest.mark.asyncio
    async def test_fail_open_sends_item_to_llm(self, worker, session_maker):
        from models import Item, Priority

        item_id = await self._seed_item(session_maker)
        proc = self._processor()
        down = Stage2Result(keep=True, decision="fail_open", error="ConnectError: down")
        with self._stage2(down):
            assert await worker._process_items([item_id], proc, is_fresh=True) == 1

        proc.analyze_from_data_with_messages.assert_awaited_once()
        assert worker._stats["stage2_fail_open"] == 1
        assert worker._stats["stage2_skipped"] == 0
        async with session_maker() as db:
            item = await db.get(Item, item_id)
            assert item.priority == Priority.MEDIUM
            assert item.needs_llm_processing is False
            assert item.metadata_["stage2_filter"]["decision"] == "fail_open"

    @pytest.mark.asyncio
    async def test_real_client_unreachable_fails_open(self, worker, session_maker):
        """End-to-end through the real client: nothing listens on the port."""
        from models import Item, Priority

        item_id = await self._seed_item(session_maker)
        proc = self._processor()
        f = Stage2Filter("http://127.0.0.1:1", timeout=2.0)
        with patch("services.llm_worker.get_stage2_filter", return_value=f):
            assert await worker._process_items([item_id], proc, is_fresh=True) == 1
        await f.close()
        proc.analyze_from_data_with_messages.assert_awaited_once()
        async with session_maker() as db:
            assert (await db.get(Item, item_id)).priority == Priority.MEDIUM
