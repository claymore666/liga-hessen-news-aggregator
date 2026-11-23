"""Tests for RelevanceFilter.find_duplicates_by_id (id-based duplicate lookup)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.relevance_filter import RelevanceFilter


def _filter_with_response(payload: dict | None = None, error: Exception | None = None):
    """Build a RelevanceFilter whose httpx client is replaced by a mock."""
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None

    client = MagicMock()
    client.is_closed = False
    client.post = AsyncMock(side_effect=error) if error else AsyncMock(return_value=response)

    relevance_filter = RelevanceFilter(base_url="http://classifier:8082/")
    relevance_filter._client = client
    return relevance_filter, client


def _dup(item_id: str, score: float) -> dict:
    return {"id": item_id, "title": f"Title {item_id}", "score": score, "snippet": "", "metadata": {}}


class TestFindDuplicatesById:
    async def test_posts_ids_as_strings(self):
        relevance_filter, client = _filter_with_response(
            {"indexed": True, "duplicates": [], "has_duplicates": False}
        )

        await relevance_filter.find_duplicates_by_id(42, [7, 8, 9], threshold=0.8, n_results=3)

        client.post.assert_awaited_once()
        args, kwargs = client.post.call_args
        assert args[0] == "http://classifier:8082/find-duplicates-by-id"
        assert kwargs["json"] == {
            "id": "42",
            "candidate_ids": ["7", "8", "9"],
            "threshold": 0.8,
            "n_results": 3,
        }

    async def test_default_threshold_and_n_results(self):
        relevance_filter, client = _filter_with_response(
            {"indexed": True, "duplicates": [], "has_duplicates": False}
        )

        await relevance_filter.find_duplicates_by_id(1, [2])

        payload = client.post.call_args.kwargs["json"]
        assert payload["threshold"] == 0.75
        assert payload["n_results"] == 5

    async def test_returns_duplicates_sorted_by_score_desc(self):
        relevance_filter, _ = _filter_with_response(
            {
                "indexed": True,
                "duplicates": [_dup("7", 0.81), _dup("9", 0.97), _dup("8", 0.9)],
                "has_duplicates": True,
            }
        )

        result = await relevance_filter.find_duplicates_by_id(42, [7, 8, 9])

        assert [d["id"] for d in result] == ["9", "8", "7"]
        assert result[0]["score"] == 0.97

    async def test_returns_empty_list_when_no_duplicates(self):
        relevance_filter, _ = _filter_with_response(
            {"indexed": True, "duplicates": [], "has_duplicates": False}
        )

        result = await relevance_filter.find_duplicates_by_id(42, [7])

        assert result == []

    async def test_returns_none_when_item_not_indexed(self):
        relevance_filter, _ = _filter_with_response(
            {"indexed": False, "duplicates": [], "has_duplicates": False}
        )

        result = await relevance_filter.find_duplicates_by_id(42, [7])

        assert result is None

    async def test_returns_none_and_warns_on_connection_error(self, caplog):
        relevance_filter, _ = _filter_with_response(error=httpx.ConnectError("classifier down"))

        with caplog.at_level(logging.WARNING, logger="services.relevance_filter"):
            result = await relevance_filter.find_duplicates_by_id(42, [7])

        assert result is None
        assert any("42" in r.getMessage() for r in caplog.records)

    async def test_returns_none_and_warns_on_http_error(self, caplog):
        relevance_filter, client = _filter_with_response({"detail": "boom"})
        client.post.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )

        with caplog.at_level(logging.WARNING, logger="services.relevance_filter"):
            result = await relevance_filter.find_duplicates_by_id(42, [7])

        assert result is None
        assert caplog.records

    async def test_raise_on_error_reraises_a_failed_call(self, caplog):
        relevance_filter, _ = _filter_with_response(error=httpx.ConnectError("classifier down"))

        with caplog.at_level(logging.WARNING, logger="services.relevance_filter"):
            with pytest.raises(httpx.ConnectError):
                await relevance_filter.find_duplicates_by_id(42, [7], raise_on_error=True)

        assert any("42" in r.getMessage() for r in caplog.records)

    async def test_raise_on_error_reraises_an_http_error(self):
        relevance_filter, client = _filter_with_response({"detail": "boom"})
        client.post.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )

        with pytest.raises(httpx.HTTPStatusError):
            await relevance_filter.find_duplicates_by_id(42, [7], raise_on_error=True)

    async def test_raise_on_error_still_returns_none_when_not_indexed(self):
        relevance_filter, client = _filter_with_response(
            {"indexed": False, "duplicates": [], "has_duplicates": False}
        )

        result = await relevance_filter.find_duplicates_by_id(42, [7], raise_on_error=True)

        assert result is None
        # The flag is local to the client, not part of the request
        assert "raise_on_error" not in client.post.call_args.kwargs["json"]

    async def test_raise_on_error_returns_matches_unchanged(self):
        relevance_filter, _ = _filter_with_response(
            {"indexed": True, "duplicates": [_dup("7", 0.81), _dup("9", 0.97)], "has_duplicates": True}
        )

        result = await relevance_filter.find_duplicates_by_id(42, [7, 9], raise_on_error=True)

        assert [d["id"] for d in result] == ["9", "7"]


class TestIndexItemsBatch:
    """index_items_batch: 0 means "nothing new" unless the call failed."""

    ITEMS = [{"id": "1", "title": "T", "content": "C", "metadata": {}}]

    async def test_returns_number_of_new_items(self):
        relevance_filter, client = _filter_with_response({"indexed": 1, "total_in_store": 10})

        assert await relevance_filter.index_items_batch(self.ITEMS) == 1

        args, kwargs = client.post.call_args
        assert args[0] == "http://classifier:8082/index/batch"
        assert kwargs["json"] == {"items": self.ITEMS}

    async def test_zero_new_on_success_does_not_raise(self):
        relevance_filter, _ = _filter_with_response({"indexed": 0, "total_in_store": 10})

        assert await relevance_filter.index_items_batch(self.ITEMS, raise_on_error=True) == 0

    async def test_returns_zero_and_warns_on_failure_by_default(self, caplog):
        relevance_filter, _ = _filter_with_response(error=httpx.ConnectError("classifier down"))

        with caplog.at_level(logging.WARNING, logger="services.relevance_filter"):
            assert await relevance_filter.index_items_batch(self.ITEMS) == 0

        assert any("classifier down" in r.getMessage() for r in caplog.records)

    async def test_raise_on_error_reraises_a_connection_error(self, caplog):
        relevance_filter, _ = _filter_with_response(error=httpx.ConnectError("classifier down"))

        with caplog.at_level(logging.WARNING, logger="services.relevance_filter"):
            with pytest.raises(httpx.ConnectError):
                await relevance_filter.index_items_batch(self.ITEMS, raise_on_error=True)

        assert any(r.levelno == logging.WARNING for r in caplog.records)

    async def test_raise_on_error_reraises_a_timeout(self):
        relevance_filter, _ = _filter_with_response(error=httpx.ReadTimeout(""))

        with pytest.raises(httpx.ReadTimeout):
            await relevance_filter.index_items_batch(self.ITEMS, raise_on_error=True)

    async def test_raise_on_error_reraises_an_http_error(self):
        relevance_filter, client = _filter_with_response({"detail": "boom"})
        client.post.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )

        with pytest.raises(httpx.HTTPStatusError):
            await relevance_filter.index_items_batch(self.ITEMS, raise_on_error=True)
