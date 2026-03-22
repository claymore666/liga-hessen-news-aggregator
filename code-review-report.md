# Code Review Report

**Project**: Liga Hessen News Aggregator
**Language(s)**: Python (FastAPI backend), Vue.js (frontend)
**Date**: 2026-03-15
**Scope**: Full backend review (services, API, connectors, LLM, tests, Docker)
**Files reviewed**: ~90 Python files
**Lines of code**: ~27,000 (backend), ~3,000 (frontend)

## Executive Summary

The codebase is well-structured with solid test coverage (28 test files, good use of fixtures and mocking). The most significant risks are **missing authentication on mutating endpoints** (sources, rules, items bulk ops), **XSS in email HTML output**, and **metadata race conditions** in the scheduler retry path. The previous review's fixes (json_merge in dedup_worker, auth on email/proxies, browser_pool for LinkedIn) were well-executed. The single highest-impact change would be adding auth to all write endpoints — several CRUD routers are fully unauthenticated.

## Findings

### 🔴 Critical

#### C-1 — XSS in briefing email HTML output — `services/email.py:79-85`

`_format_item_html` interpolates `item.title`, `item.url`, `item.source.name`, and `item.summary` directly into HTML without escaping. A malicious feed source could inject HTML/JS into briefing emails sent to all Liga recipients.

**Fix:**
```python
from html import escape
html += f'<strong><a href="{escape(item.url)}" style="color: #0369a1;">{escape(item.title)}</a></strong>'
if item.source:
    html += f'<br><span style="color: #6b7280; font-size: 12px;">{escape(item.source.name)}</span>'
if self.config.include_summary and item.summary:
    html += f'<br><span style="color: #374151;">{escape(item.summary)}</span>'
```

#### C-2 — Unauthenticated source/channel/rule CRUD — `api/sources.py`, `api/rules.py`

The `sources.py` router has no `dependencies=[Depends(require_admin_key)]`. Anyone can create, modify, delete sources/channels, or trigger fetches. Same for `rules.py`. An unauthenticated user can delete all sources (cascading to channels and items) or add malicious feed URLs.

**Fix:** Add `dependencies=[Depends(require_admin_key)]` to write endpoints on both routers, or to the routers themselves if all endpoints should be protected.

#### C-3 — OpenRouter crashes on empty/error responses — `services/llm/openrouter.py:111,158`

OpenRouter can return 200 with `{"error": {...}}` or `{"choices": []}` (content moderation, model overloaded). The code does `data["choices"][0]["message"]["content"]` without checking, causing `IndexError` that kills the entire LLM worker batch.

**Fix:**
```python
choices = data.get("choices", [])
if not choices:
    error_info = data.get("error", {})
    raise RuntimeError(f"OpenRouter returned no choices: {error_info}")
content = choices[0].get("message", {}).get("content", "")
```

#### C-4 — Timing-attack-vulnerable admin key comparison — `api/auth.py:16`

`x_admin_key != settings.admin_api_key` uses Python's default string comparison which short-circuits on first mismatched byte. An attacker can progressively guess the key by measuring response times.

**Fix:**
```python
import hmac
if not hmac.compare_digest(x_admin_key, settings.admin_api_key):
    raise HTTPException(status_code=403, detail="Invalid admin key")
```

#### C-5 — Unbounded bulk ID lists — DoS — `api/items.py:19-21`

`BulkUpdateRequest` accepts `ids: list[int]` with no upper bound. A client can send millions of IDs, building an enormous SQL `IN (...)` clause that exhausts memory or locks the DB.

**Fix:** `ids: list[int] = Field(..., max_length=500)`

#### C-6 — X scraper unbound `context` in `finally` — `connectors/x_scraper.py:183,233`

Same pattern as the Instagram bug fixed in the previous review. If `browser.new_context()` raises, the `finally` block tries `await context.close()` on an unassigned variable → `UnboundLocalError`, masking the real error.

**Fix:** Add `context = None` before `try`, guard with `if context:` in `finally`.

### 🟡 Warning

#### W-1 — `retry_llm_processing` overwrites metadata (no json_merge) — `services/scheduler.py:593`

`item.metadata_["llm_analysis"] = {...}` does a direct dict assignment on the ORM object. If the classifier worker writes to the same item's metadata concurrently, those writes are lost. The LLM worker correctly uses `json_merge()` — this older scheduler function does not.

**Fix:** Use `json_merge` like the LLM worker:
```python
await db.execute(
    update(Item).where(Item.id == item.id).values(
        metadata_=json_merge(Item.metadata_, {"llm_analysis": {...}}),
        needs_llm_processing=False,
    )
)
```

#### W-2 — Blocking socket in async context — `services/proxy_manager.py:298-318`

`validate_https_tunnel` uses blocking `socket.connect()` / `socket.recv()`. During proxy validation, the event loop stalls for up to `5s × batch_size`. With 25 proxies, worst case 125s of blocking.

**Fix:** Run in executor: `await loop.run_in_executor(None, self._validate_https_tunnel_sync, proxy)`

#### W-3 — Proxy socket not closed on error/non-200 paths — `services/proxy_manager.py:299-318`

`sock.close()` only called on success path. Failed validations leak file descriptors. With hundreds of proxies tested per cycle, this can exhaust fd limits.

**Fix:** Use `try/finally` with `sock.close()`.

#### W-4 — RelevanceFilter leaked per channel fetch — `services/scheduler.py:206`

`fetch_channel` creates a new `RelevanceFilter` (with httpx client) per channel, never closed. ~50 orphaned clients per fetch cycle leaking TCP connections.

**Fix:** Create the filter once in `fetch_due_channels` and pass it through, or reuse the classifier worker's singleton instance.

#### W-5 — SSL verification fully disabled for `verify_ssl=False` feeds — `connectors/rss.py:23-35`

`create_legacy_ssl_context()` sets `verify_mode = ssl.CERT_NONE`. Makes connections vulnerable to MITM. One misconfigured channel is enough.

**Fix:** Lower cipher security level but keep cert verification enabled.

#### W-6 — SSRF via crafted Mastodon handle — `connectors/mastodon.py:36-41`

Handle validator only checks for `@`. A handle like `user@169.254.169.254` passes validation and the backend fetches the constructed URL, enabling SSRF against internal services.

**Fix:** Validate instance domain against a pattern (no IPs, no localhost, no private ranges).

#### W-7 — RSS/Mastodon timezone bug — `connectors/rss.py:101,106`, `connectors/mastodon.py:119`

`mktime(entry.published_parsed)` interprets feedparser's UTC time as local time. Publication dates shifted by 1-2 hours on `TZ=Europe/Berlin` servers, affecting dedup windows and ordering.

**Fix:** Use `calendar.timegm()` instead of `time.mktime()`.

#### W-8 — Pipeline mid-transaction commit breaks atomicity — `services/pipeline.py:620`

Intermediate `await self.db.commit()` for vectordb_indexed flags commits before the caller's final commit. If the second commit fails, the channel appears never-fetched but items are already indexed.

**Fix:** Change to `await self.db.flush()`.

#### W-9 — PostgreSQL memory config exceeds container limit — `docker-compose.prod.yml:63`

`shared_buffers=512MB` equals the entire `mem_limit: 512m`, leaving zero for connections and sort ops. `effective_cache_size=3GB` tells the planner 3GB is available when only 512MB exists.

**Fix:** `shared_buffers=128MB`, `effective_cache_size=384MB`, or increase `mem_limit` to 1g.

#### W-10 — Error details leak internal exceptions — `api/sources.py:525`, `api/digest.py:67`, `api/llm.py:109`

Multiple endpoints return `str(e)` in HTTP responses, potentially leaking internal paths, connection strings, or stack traces.

**Fix:** Log full exception server-side, return generic message to client.

#### W-11 — ILIKE search doesn't escape `%` and `_` wildcards — `api/items.py:153-156`

Search input is wrapped with `%` for ILIKE but special characters aren't escaped, producing incorrect results for queries containing `%` or `_`.

**Fix:** `escaped = search.replace("%", "\\%").replace("_", "\\_")`

#### W-12 — Additional endpoints missing auth — `api/items.py`, `api/motd.py`, `api/analytics.py`

`POST /items/{id}/refetch`, `POST /items/bulk-update`, `POST /items/mark-all-read`, `GET /motd/history`, `GET /analytics/recent-errors` — all write or sensitive-read ops accessible without auth.

**Fix:** Add `dependencies=[Depends(require_admin_key)]` to these endpoints.

#### W-13 — `datetime.utcnow()` deprecated throughout — multiple files

Used across ~30+ locations. Returns naive datetimes that can cause `TypeError` when compared with timezone-aware datetimes from other sources (Instagram uses `datetime.now(UTC)`).

**Fix:** Replace with `datetime.now(UTC)` project-wide. Low urgency since the codebase is internally consistent.

#### W-14 — Containers run as root — `backend/Dockerfile:49`, `classifier-api/Dockerfile:20`

Both the backend and classifier containers run as root. Backend comment says "for Playwright compatibility" but this is unnecessary.

**Fix:** Add non-root user in Dockerfiles.

#### W-15 — No startup enforcement of ADMIN_API_KEY — `config.py`

If `ADMIN_API_KEY` is unset, all admin endpoints are unauthenticated. No warning logged at startup.

**Fix:** Log a loud warning (or refuse to start in prod mode) if `ADMIN_API_KEY` is empty.

### 🔵 Info

#### I-1 — OpenRouterProvider creates new httpx client per request — `services/llm/openrouter.py:99,147`

Unlike OllamaProvider (now persistent), OpenRouter still creates `async with httpx.AsyncClient()` per call, adding ~100-300ms TLS overhead per request.

#### I-2 — OllamaProvider `close()` never called on shutdown — `services/llm/ollama.py`

`LLMService` never calls `close()` on its providers. Persistent client leaks on process exit.

#### I-3 — `_poll_commands` calls `self.stop()` which cancels itself — `llm_worker.py:282`, `classifier_worker.py:261`, `dedup_worker.py:236`

Maintenance hazard — task cancels itself from within.

#### I-4 — Dedup worker `_check_vectordb_sync` creates its own httpx client — `dedup_worker.py:741`

Bypasses the singleton classifier client. One leaked client per day.

#### I-5 — Rate-limited fresh items dropped from queue — `llm_worker.py:780`

On `RateLimitError`, remaining fresh items are silently lost from the queue, demoted to slower backlog processing.

#### I-6 — Dead imports — `connectors/instagram_scraper.py:7`, `connectors/x_scraper.py:5`

Unused `import asyncio` in both files.

#### I-7 — 8 skipped tests without tracking — `test_scheduler_api.py`, `test_misc_api.py`

Tests skipped for "APScheduler event loop issues" and "Makes network calls" with no plan to re-enable.

#### I-8 — Duplicate test coverage — `test_api_channels.py` vs `test_sources_api.py`

Significant overlap between combined and dedicated test files.

#### I-9 — `requirements.txt` uses loose version ranges — no lock file

Builds are not fully reproducible. Two builds on different days may get different patch versions.

## Metrics Summary

| Category | Issues | Critical | Warning | Info |
|----------|--------|----------|---------|------|
| Security (auth, XSS, SSRF) | 8 | 3 | 3 | 0 |
| Error Handling & Robustness | 5 | 2 | 2 | 1 |
| Concurrency / Race Conditions | 3 | 0 | 2 | 1 |
| Resource Leaks | 4 | 0 | 2 | 2 |
| Performance | 2 | 0 | 1 | 1 |
| Input Validation | 3 | 1 | 1 | 0 |
| Correctness (timezone, etc.) | 2 | 0 | 2 | 0 |
| Docker / Infrastructure | 3 | 0 | 2 | 0 |
| Testing | 3 | 0 | 0 | 3 |
| Code Quality | 1 | 0 | 1 | 1 |
| **Total** | **34** | **6** | **15** | **9** |

## What's Done Well

1. **Comprehensive test suite** — 28 test files covering API endpoints, connectors, pipeline, workers, and models. Good use of async fixtures and mocking.
2. **Atomic metadata merging** — The `json_merge()` pattern in the LLM worker and (now) dedup worker is a solid approach to concurrent JSONB updates.
3. **Graceful degradation** — Workers handle classifier unavailability, rate limits, and consecutive errors with exponential backoff. The dedup worker's phased approach (Phase 1 without GPU, Phase 2 when available) is well-designed.
4. **Browser pool** — Shared Playwright instance with concurrency limiting prevents resource exhaustion from multiple scrapers.

## Top Recommendations

1. **Add auth to all write endpoints** (C-2, W-12, W-15) — Add `require_admin_key` to sources, rules, items bulk ops, refetch, motd. Add startup warning if key is unset. ~1 hour.
2. **Fix XSS in email HTML** (C-1) — Add `html.escape()` to all interpolated values in `email.py` and verify `digest_email.py` (which already uses `escape()`). ~15 minutes.
3. **Guard OpenRouter empty responses** (C-3) — Add defensive checks on `data["choices"]`. ~15 minutes.
4. **Use `hmac.compare_digest` for auth** (C-4) — One-line fix. ~5 minutes.
5. **Fix X scraper unbound context + proxy socket leaks** (C-6, W-3) — Same pattern as Instagram fix. ~30 minutes.
6. **Switch `retry_llm_processing` to json_merge** (W-1) — Same pattern as the dedup_worker fix. ~15 minutes.
7. **Fix RSS/Mastodon timezone bug** (W-7) — Replace `mktime` with `timegm`. ~15 minutes.
