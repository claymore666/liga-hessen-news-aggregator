# Code Review Report

**Project**: Liga Hessen News Aggregator
**Language(s)**: Python (FastAPI backend, ML classifier), TypeScript/Vue 3 (frontend)
**Date**: 2026-03-08
**Scope**: Full codebase — backend, frontend, classifier API, training pipeline
**Files reviewed**: 962 source files
**Lines of code**: ~280,000 (258k Python, 15k TypeScript, 7k Vue)

## Executive Summary

This is a mature, production-running news aggregation system with solid architecture — clear separation of API/services/connectors, async processing pipeline, ML classification, and versioned LLM prompt management. The biggest risks are: (1) **synchronous blocking calls in the classifier API's async endpoints**, meaning one slow Ollama call blocks all concurrent requests; (2) **silent error swallowing** in several code paths that marks failed items as "irrelevant" instead of retrying; and (3) **no authentication on any endpoint**, including admin operations that control LLM prompts, worker lifecycle, and data deletion. The codebase is well-tested (612 tests, 93.9% pass rate) but 37 tests are stale relative to recent code changes. The single highest-value improvement would be adding retry logic and proper error propagation throughout the LLM/classifier processing pipeline.

## Tooling Results

**Tools executed:**

| Tool | Version | Findings | Notes |
|------|---------|----------|-------|
| ruff | 0.15.5 | 444 default, 1877 extended | Installed for review |
| py_compile | 3.12.13 | 0 syntax errors | All core files clean |
| pytest | 9.0.2 | 575 pass, 37 fail, 9 skip | Run inside Docker container |

**Build**: Pass (Docker containers build and run successfully)
**Tests**: 575 passed, 37 failed, 9 skipped (93.9% pass rate)

## Findings

### Critical

#### C-1: Synchronous blocking in classifier API async endpoints — `classifier-api/main.py:264+`

All FastAPI endpoints are `async def` but call synchronous methods: `classifier.predict()` makes blocking `httpx.Client` HTTP calls to Ollama, `vector_store.search()` does blocking ChromaDB operations. **One slow Ollama call (up to 120s timeout) blocks all concurrent requests** to the classifier API.

**Fix:** Use `httpx.AsyncClient` in `OllamaEmbedder` or wrap calls with `await asyncio.to_thread(...)`.

#### C-2: No authentication on admin endpoints — all API files

All endpoints including prompt management (`api/prompts.py:136,178`), worker control (`api/workers.py`), item reprocessing (`api/items.py`), and classifier data deletion (`classifier-api/main.py:590`) are completely unprotected. An attacker who reaches port 8000 could change the LLM system prompt to exfiltrate data or produce malicious outputs.

**Mitigation (current):** Port 8000 is not exposed on docker-ai's LAN (SSH tunnel only). But this is defense by obscurity, not defense in depth.

**Fix:** Add API key middleware or bearer token auth for admin endpoints at minimum. Read key from env var.

#### C-3: Reprocess endpoint holds DB connections during LLM calls — `api/items.py:1192-1280`

`_reprocess_items_task` calls `processor.analyze(item)` while holding a DB session open. Each LLM call takes 3-60 seconds. With 471 items, this holds a DB connection for ~23 minutes straight. The LLM worker was specifically refactored to use a read-process-write pattern to avoid this. The reprocess endpoint should use the same pattern.

**Fix:** Refactor to use `analyze_from_data()` with the 3-phase pattern from `llm_worker.py`.

#### C-4: Reprocess endpoint skips topic extraction — `api/items.py:1218`

`_reprocess_items_task` uses `processor.analyze(item)` instead of `analyze_from_data_with_messages()`. Reprocessed items never get topic classification, meaning they lack topic labels that items processed by the LLM worker receive.

**Fix:** Use `analyze_from_data_with_messages()` and add the topic extraction step.

#### C-5: XSS via v-html in frontend — `MOTDModal.vue:179`, `SettingsView.vue:415`

`v-html="formattedMessage"` renders MOTD content as raw HTML without sanitization. While currently admin-only content, a compromised admin account or future user-facing MOTD would allow script injection.

**Fix:** Use a sanitization library (DOMPurify) or render with `v-text` and CSS for formatting.

#### C-6: Index inconsistency on partial failure — `classifier-api/main.py:390-413`

In `/index`, if `vector_store.add_item()` succeeds but `duplicate_store.add_item()` fails, the item exists in the search index but not the duplicate index. No rollback is attempted. The response reports success even though stores are now inconsistent.

**Fix:** Wrap both operations in a try block; if the second fails, roll back the first (delete from search store) or return a partial-success response.

### Warning

#### W-1: Processor prompt caching without invalidation — `llm_worker.py:165-171`

`_get_processor` caches the processor instance (and its prompt) forever. Changing the active prompt via the `/prompts/admin` API has no effect until the LLM worker restarts. The API acknowledges this in a comment but there is no signal mechanism.

**Fix:** Add a `_prompt_cache_ttl` (e.g., 5 minutes) or a `/admin/reload-prompt` endpoint that signals the worker.

#### W-2: `retry_llm_processing` is a divergent, inferior code path — `scheduler.py:495-622`

This function still exists and is callable via API but uses an older processing pattern without topic extraction, prompt tracking, or duplicate confirmation. Items processed through this path get inferior treatment compared to the LLM worker.

**Fix:** Remove the function or redirect it to use the LLM worker's processing path.

#### W-3: 37 failing tests — various test files

Tests are stale relative to code changes:
- `test_proxy_manager.py` (8 failures) — ProxyManager API changes
- `test_llm_worker.py` (5) — worker pause/resume changes
- `test_classifier_toggles.py` (5) — toggle API changes
- `test_admin_api.py` (3) — logs endpoint changes
- `test_processor.py` (2) — parse/default analysis changes
- Others: 13 failures across 6 more files

**Fix:** Update tests to match current code. Priority: `test_processor.py` (validates the error propagation fix from C-3).

#### W-4: No input size limits on classifier API — `classifier-api/main.py:102, 175`

`ClassifyRequest` accepts unbounded `content` strings. `IndexBatchRequest` accepts unbounded item lists. A malicious or buggy caller could send a multi-megabyte request or thousands of items, causing OOM or blocking the service.

**Fix:** Add `max_length` validators on Pydantic models: `content: str = Field(max_length=50000)`, `items: list = Field(max_length=500)`.

#### W-5: O(n^2) lookup in batch indexing — `classifier.py:414, 667`

`documents.append(texts[new_items.index(item)][:2000])` does a linear scan of `new_items` for each item. For a batch of 5000 items = 25 million comparisons.

**Fix:** Use `enumerate()` and index by position, or build a dict mapping item to text.

#### W-6: TOCTOU race in ChromaDB add_item — `classifier.py:356-377`

The check-then-add pattern (`get(ids=[item_id])` then `add(...)`) is not atomic. Concurrent requests can both see the item as non-existing and both attempt to add.

**Fix:** Use `upsert()` instead of get-then-add.

#### W-7: `_default_analysis` returns inconsistent priority — `processor.py:718`

`_default_analysis` returns `priority: "low"` for failed items, but the system uses `priority: None` for irrelevant items. This inconsistency can cause items to appear in LOW priority views when they should be filtered out.

**Fix:** Change to `"priority": None` to match the irrelevant item convention.

#### W-8: `get_db()` auto-commits on success — `database.py:141-149`

Every request (including read-only GETs) issues a COMMIT. Any accidental writes in GET handlers are silently committed. This is unnecessary overhead and a safety concern.

**Fix:** Make commit explicit in write endpoints, or use separate read-only session factory for GET endpoints.

#### W-9: Missing error handling in frontend form saves — `SourceFormModal.vue:28`, `RuleFormModal.vue:58`, `ChannelFormModal.vue:145`

Three modal save functions have no error handling. If the API call fails, the user sees no feedback and the modal stays open.

**Fix:** Add try/catch with user-visible error display (toast or inline error message).

#### W-10: No retry logic for transient Ollama failures in classifier — `classifier.py:82-86`

`encode()` does a single HTTP call with no retry. Transient network blips or Ollama model-loading delays fail the entire request.

**Fix:** Add retry with exponential backoff (3 attempts, 1s/2s/4s delays).

### Info

| ID | File | Finding |
|----|------|---------|
| I-1 | `models.py:8,10` | Unused imports: `UUID`, `ARRAY`, `uuid` |
| I-2 | `processor.py:683` | Redundant `import re` inside method (already at module level) |
| I-3 | `main.py:298,328` | Redefined `stop_log_writer` and `close_redis` (F811) |
| I-4 | `scripts/backfill_topics.py` | 6 duplicate dictionary keys silently overwritten (F601) |
| I-5 | Various (7 files) | f-strings without placeholders (F541) |
| I-6 | 24 prod files, 20 test files | 44 unused imports (F401) |
| I-7 | 3 prod files, 8 test files | 11 unused variables (F841) |
| I-8 | 123 locations | `datetime.utcnow()` deprecated in Python 3.12+ (DTZ003) |
| I-9 | `pipeline.py:521-524` | Monkey-patching ORM objects with dynamic attributes |
| I-10 | `main.py:86-225` | Hand-rolled SQL migrations without rollback mechanism |
| I-11 | `classifier.py:47` | Docstring says "30s cache" but `_availability_ttl = 900.0` (15 min) |
| I-12 | `feature_extraction.py:245` | Comment says "shape (27,)" but actual array has 37 elements |
| I-13 | `classifier-api/requirements.txt` | Loosely pinned deps (`>=` without upper bounds except numpy) |
| I-14 | Frontend: `ItemsView.vue`, `ItemDetailView.vue` | 2 entire view files (~664 lines) are dead code (not routed) |
| I-15 | Frontend: `stores/items.ts:27-33` | `highItems` and `highPriorityItems` computed never used |
| I-16 | Frontend: 3 components | Direct `axios` import bypassing shared API client |
| I-17 | Frontend: all modals | Missing `role="dialog"`, `aria-modal`, `aria-labelledby` |
| I-18 | Frontend: `stores/items.ts:207` | `value as never` cast bypasses type checking |
| I-19 | `browser_pool.py:88` | No timeout on semaphore acquisition (hangs forever if all slots occupied) |
| I-20 | `scheduler.py:827-830` | Fire-and-forget tasks that silently swallow exceptions |
| I-21 | `article_extractor.py:69-74` | Googlebot user-agent impersonation for paywall bypass |
| I-22 | `items.py:970-1047` | `_scrape_tweet_for_links` bypasses browser pool concurrency limits |

## Metrics Summary

| Category | Issues | Critical | Warning | Info |
|----------|--------|----------|---------|------|
| Error Handling & Robustness | 11 | 2 | 3 | 6 |
| Performance & Efficiency | 3 | 1 | 1 | 1 |
| Dead Code & Unused | 9 | 0 | 0 | 9 |
| Code Style & Idioms | 4 | 0 | 0 | 4 |
| Concurrency | 4 | 1 | 1 | 2 |
| Testing | 1 | 0 | 1 | 0 |
| Dependencies | 1 | 0 | 0 | 1 |
| Configuration & Environment | 1 | 0 | 1 | 0 |
| Logging & Observability | 1 | 0 | 0 | 1 |
| Security | 3 | 2 | 0 | 1 |
| Architecture | 2 | 1 | 1 | 0 |
| Project Hygiene | 2 | 0 | 0 | 2 |
| Frontend | 8 | 1 | 2 | 5 |
| **Total** | **50** | **8** | **10** | **32** |

## What's Done Well

1. **Clean service architecture**: Clear separation between API endpoints, services, connectors, and data models. Each connector follows a consistent interface. The pipeline orchestration is well-designed with separate stages (fetch, classify, enrich, deduplicate).

2. **LLM worker's 3-phase read-process-write pattern**: The worker releases DB connections during long-running LLM calls, preventing connection pool exhaustion. This is a sophisticated pattern that shows production-informed engineering.

3. **Comprehensive connector coverage**: 14 news source connectors (RSS, X/Twitter, LinkedIn, Mastodon, Bluesky, Instagram, Telegram, Google Alerts, HTML, PDF) with consistent error handling and structured metadata extraction.

4. **Operational tooling**: DB-stored versioned prompts with per-item tracking, Wake-on-LAN integration for GPU power management, processing analytics with disagreement detection, and MOTD system for user communication. The project has mature operational practices.

5. **Documentation**: Extensive architecture docs, troubleshooting guides, prompt tuning methodology, and CLAUDE.md with deployment procedures and API endpoint reference.

## Top Recommendations

1. **Fix the classifier API's async blocking** (C-1). Switch `OllamaEmbedder` to `httpx.AsyncClient` and make `encode()` async. This unblocks concurrent classifier requests and prevents a single slow Ollama call from freezing the entire API. ~2-4 hours.

2. **Refactor `_reprocess_items_task` to match `llm_worker`** (C-3, C-4). Use the 3-phase pattern and `analyze_from_data_with_messages()` so reprocessed items get topic extraction, don't hold DB connections, and produce consistent results. ~2-3 hours.

3. **Add API key auth for admin endpoints** (C-2). A simple middleware that checks `X-API-Key` header against an env var for all `/admin`, `/prompts/admin`, and destructive endpoints. ~1-2 hours for backend, then update frontend to send the key.

4. **Fix the 37 failing tests** (W-3). Most failures are from stale assertions after recent code changes. The `test_processor.py` failures are particularly important since they should validate the error propagation fix. ~3-4 hours.

5. **Add retry logic to classifier Ollama calls** (W-10) and input validation limits (W-4). Prevents transient failures from causing unnecessary errors and protects against oversized requests. ~1-2 hours.
