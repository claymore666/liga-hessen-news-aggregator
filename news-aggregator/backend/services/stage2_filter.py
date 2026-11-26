"""Stage-2 junk filter client (gbert student on gpu1).

The LLM worker asks this service, right before the LLM, whether an item is junk.
The service lives in relevance-tuner/services/stage2-filter and runs on gpu1 only.

FAIL OPEN: disabled, unreachable, timeout, HTTP error or malformed answer ->
``check`` returns a result with ``keep=True`` and ``decision="fail_open"``; the
item goes to the LLM exactly as without the filter. Only an explicit
``keep: false`` from the service skips the LLM.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

import httpx

from config import settings

logger = logging.getLogger(__name__)

SKIP_REASON = "stage2_below_threshold"
EVENT_STAGE2_FILTERED = "stage2_filtered"


@dataclass
class Stage2Result:
    keep: bool
    decision: str  # "keep" | "skip" | "fail_open"
    mode: str | None = None  # "ensemble" | "student"
    score: float | None = None
    threshold: float | None = None
    student_score: float | None = None
    model: str | None = None
    error: str | None = None

    def as_metadata(self, checked_at: str) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["checked_at"] = checked_at
        return d


class Stage2Filter:
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=min(3.0, self.timeout))
            )
        return self._client

    async def close(self):
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def check(self, title: str, content: str, pre_filter: dict | None) -> Stage2Result | None:
        """None when disabled. Never raises."""
        if not self.enabled:
            return None
        pre_filter = pre_filter or {}
        payload = {
            "title": title or "",
            "content": (content or "")[:200000],
            "m1_score": pre_filter.get("relevance_raw_score"),
            "m1_model": pre_filter.get("gate_model"),
        }
        try:
            client = await self._get_client()
            resp = await client.post(f"{self.base_url}/score", json=payload)
            resp.raise_for_status()
            data = resp.json()
            keep = data["keep"]
            if not isinstance(keep, bool):
                raise ValueError(f"keep is not a bool: {keep!r}")
            return Stage2Result(
                keep=keep,
                decision="keep" if keep else "skip",
                mode=data.get("mode"),
                score=data.get("score"),
                threshold=data.get("threshold"),
                student_score=data.get("student_score"),
                model=data.get("model"),
            )
        except Exception as e:  # fail open on anything
            logger.warning(f"Stage-2 filter unavailable, failing open: {type(e).__name__}: {e}")
            return Stage2Result(keep=True, decision="fail_open", error=f"{type(e).__name__}: {e}"[:300])


_filter: Stage2Filter | None = None


def get_stage2_filter() -> Stage2Filter:
    global _filter
    if _filter is None or _filter.base_url != settings.stage2_filter_url.rstrip("/"):
        _filter = Stage2Filter(settings.stage2_filter_url, settings.stage2_filter_timeout)
    return _filter
