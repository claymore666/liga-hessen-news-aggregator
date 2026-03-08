# Code Review Report

**Project**: Liga Hessen News Aggregator
**Language(s)**: Python (FastAPI backend, classifier API), TypeScript/Vue.js (frontend)
**Date**: 2026-03-08
**Scope**: Full codebase — backend services, API endpoints, frontend components, classifier API, Docker/infrastructure
**Files reviewed**: 184 source files
**Lines of code**: ~55,000

## Executive Summary

The codebase is a well-structured, production-running news aggregation system with strong architectural patterns (3-phase DB release for LLM calls, leader election for workers, parallel channel fetching). However, several **critical correctness bugs** remain: a priority mapping mismatch silently downgrades items on batch reprocess, a scheduler API endpoint references a non-existent job ID, and non-atomic metadata writes in reprocess endpoints create race conditions already solved elsewhere in the codebase. On the infrastructure side, all three Docker containers run as root, 48 MB of binary files are tracked in git, and Python dependencies lack upper version bounds. The frontend is clean TypeScript with no type errors, but has XSS surface via `v-html`, race conditions from unsuperseded API calls, and a non-functional save button left in production. The single highest-impact change would be **fixing the priority mapping in `api/llm.py`** (C-1), as it silently corrupts data quality on every batch reprocess.

## Tooling Results

**Tools executed:**

| Tool | Version | Findings | Notes |
|------|---------|----------|-------|
| ruff | 0.15.5 | 64 unused imports (F401), 128 unused variables (F841) | Backend + classifier |
| vue-tsc | (bundled) | 0 errors | Frontend passes cleanly |
| npm audit | npm 10.x | 7 vulnerabilities (3 high, 4 moderate) | minimatch ReDoS, rollup path traversal |
| pip-audit | — | Skipped | PEP 668 blocks host install |
| pytest | — | Skipped on host | 612 passed, 0 failed, 9 skipped (last Docker run) |

**Build**: Pass (all three services build successfully)
**Tests**: 612 passed, 0 failed, 9 skipped (backend, last Docker run)

## Findings

### 🔴 Critical

#### C-1: Priority mapping in `reprocess_unprocessed_items` silently downgrades every item — `api/llm.py:368-377`

The priority mapping here shifts everything down one level: `critical→HIGH, high→MEDIUM, medium→LOW, low→NONE`. Every other code path (`llm_worker.py:577-591`, `api/items.py:1294-1308`) correctly maps `critical→HIGH, high→HIGH, medium→MEDIUM, low→LOW`. Any admin batch reprocess via this endpoint systematically downgrades priorities — a HIGH-priority policy item silently becomes MEDIUM.

**Fix:**
```python
if llm_priority == "critical":
    item.priority = Priority.HIGH
elif llm_priority == "high":
    item.priority = Priority.HIGH  # was: Priority.MEDIUM
elif llm_priority == "medium":
    item.priority = Priority.MEDIUM  # was: Priority.LOW
elif llm_priority == "low":
    item.priority = Priority.LOW  # was: Priority.NONE
else:
    item.priority = Priority.NONE
```

---

#### C-2: Scheduler interval update references wrong job ID — `api/scheduler.py:84,94`

`get_job("fetch_all_sources")` and `reschedule_job("fetch_all_sources", ...)` reference a job ID that does not exist. The actual job registered in `services/scheduler.py:770` uses ID `"fetch_due_channels"`. The PUT `/scheduler/interval` endpoint always returns 404.

**Fix:** Replace `"fetch_all_sources"` with `"fetch_due_channels"` on lines 84 and 94.

---

#### C-3: Reprocess endpoints use non-atomic metadata writes — `api/items.py:1330`, `api/llm.py:382-384`

`api/items.py` reads `old_metadata` in Phase 1, processes LLM in Phase 2 (10-60s), then writes `{**old_metadata, "llm_analysis": llm_meta}` in Phase 3 using a stale snapshot. `api/llm.py` mutates `item.metadata_` in-place via ORM. Both ignore the concurrent-safe `json_merge` pattern already used in `llm_worker.py:648`.

If the classifier worker writes `pre_filter` metadata between Phase 1 and Phase 3, the classifier's update is silently overwritten.

**Fix:** Use `json_merge` with a `sql_update` statement, same pattern as `llm_worker.py` Phase 3.

---

#### C-4: `reorderRules` can produce array with `undefined` entries — `frontend/src/stores/rules.ts:112-115`

The function maps over `newOrder` IDs using `.find()` with a non-null assertion (`!`). If any ID doesn't match, `find()` returns `undefined` — the `!` suppresses the TypeScript error, corrupting reactive state and causing runtime crashes when templates iterate over `rules`.

**Fix:**
```ts
const reordered = newOrder.map(id => {
  const rule = rules.value.find(r => r.id === id)
  if (!rule) throw new Error(`Rule ${id} not found`)
  return rule
})
```

---

#### C-5: All three Docker containers run as root — all Dockerfiles

No `USER` directive in any Dockerfile. Running as root increases blast radius of container escapes.

**Fix:** Add `RUN useradd -r -s /bin/false appuser` + `USER appuser` to backend and classifier Dockerfiles. Use `USER nginx` for the frontend.

---

#### C-6: 48 MB of binary files tracked in git

- `relevance-tuner/services/classifier-api/models/embedding_classifier_nomic-v2.pkl` (34.8 MB)
- `relevance-tuner/services/classifier-api/models/embedding_classifier_nomic-v2.pkl.backup-20260116` (11.8 MB)
- `news-aggregator/backups/liga_news_20260110_175203.db.gz` (1.8 MB)

These bloat every clone permanently. The DB backup may contain PII.

**Fix:** Use Git LFS for model files. Remove backup from git with `git rm --cached`. Add `news-aggregator/backups/` to `.gitignore`.

---

#### C-7: httpx.AsyncClient never closed in classifier — `classifier-api/classifier.py:42`

`OllamaEmbedder.__init__` creates `self._client = httpx.AsyncClient(...)` but has no `close()` method. Two instances leak connections on every shutdown.

**Fix:** Add `async def close(self)` calling `await self._client.aclose()`. Call from lifespan teardown.

---

### 🟡 Warning

#### W-1: Empty `admin_api_key` disables all admin authentication — `api/auth.py:14-15`

If `ADMIN_API_KEY` is unset or empty, the guard returns immediately — unauthenticated access to all admin endpoints (config import/export, scheduler control, worker management).

**Fix:** Log a warning at startup when `admin_api_key` is empty. Consider requiring it in production.

---

#### W-2: Leader election lock file not resilient to crashes — `main.py:33-52`

If the leader worker crashes (OOM, unhandled exception), the lock file is never cleaned up. No other worker can become leader until container restart. Background workers (scheduler, LLM, classifier) stop permanently.

**Fix:** Use `fcntl.flock` (auto-releases on process death) instead of `O_CREAT | O_EXCL`.

---

#### W-3: Config import replace mode cascades deletion to all items — `api/config.py:298-313`

Replace mode deletes all sources, cascading to channels and items — destroying all historical news data with no confirmation step.

**Fix:** Add `confirm_delete_items: bool = Query(False)` parameter required for replace mode.

---

#### W-4: `reprocess_unprocessed_items` processes items synchronously in HTTP request — `api/llm.py:330-394`

Unlike `api/items.py` which uses `background_tasks.add_task(...)`, this endpoint processes all items synchronously. Each LLM call takes 10-60s, causing timeouts on any non-trivial batch.

**Fix:** Move processing loop into a BackgroundTasks handler.

---

#### W-5: LLM worker backoff sleep is not interruptible — `services/llm_worker.py:237-239`

`await asyncio.sleep(backoff)` with up to 300s. If LLM recovers during sleep, or fresh items arrive, the worker is stuck waiting.

**Fix:** Use `asyncio.wait` on a wake event with timeout.

---

#### W-6: Stale `old_metadata` and `old_priority_score` in 3-phase reprocess — `api/items.py:1261,1330`

Phase 1 captures `old_metadata` and `old_priority_score`. Phase 2 (10-60s LLM call) elapses. Phase 3 uses stale values for `max(old_priority_score, X)` comparisons.

**Fix:** Re-read current `priority_score` in Phase 3, or use SQL `GREATEST()` in the update.

---

#### W-7: `v-html` with MOTD content relies on fragile manual escaping — `frontend/src/components/MOTDModal.vue:191`

`escapeHtml()` escapes `&`, `<`, `>` but not quotes. Pattern is fragile — reordering escape and HTML construction steps would introduce XSS.

**Fix:** Use DOMPurify or refactor to Vue template rendering.

---

#### W-8: `v-html` for email preview trusts server content — `frontend/src/views/SettingsView.vue:416`

`v-html="preview.html_body"` renders server-generated HTML that includes RSS feed titles/summaries. A malicious source could inject script tags.

**Fix:** Render in a sandboxed `<iframe srcdoc="...">`.

---

#### W-9: No request cancellation for superseded API calls (race condition) — multiple frontend components

When filters change rapidly, multiple API requests fire. Slower earlier responses can overwrite fresher data.

**Files:** `TopicWordCloud.vue`, `SourceDonutChart.vue`, `NachrichtenView.vue`

**Fix:** Use `AbortController` to cancel in-flight requests when a new one starts.

---

#### W-10: `saveSettings` is a no-op with TODO in production — `frontend/src/views/SettingsView.vue:93-99`

"Einstellungen speichern" button shows success message but contains `// TODO: Implement settings API`. Users think settings are saved when they aren't.

**Fix:** Either implement the API call or disable/remove the non-functional button.

---

#### W-11: Keyboard shortcut `r` conflicts between global and page-level — `useKeyboardShortcuts.ts:67`, `NachrichtenView.vue:176`

Both global (`r` = navigate to Rules) and page-level (`r` = toggle relevance) handlers fire simultaneously on the Nachrichten page.

**Fix:** Have page-specific shortcuts call `event.stopImmediatePropagation()`.

---

#### W-12: Shared `loading` flag across concurrent store operations — `frontend/src/stores/sources.ts`

A single `loading` ref shared by 5 operations. First to complete sets `loading = false`, hiding in-progress state of concurrent operations.

**Fix:** Use a counter: `loadingCount++` on start, `loadingCount--` in `finally`.

---

#### W-13: Classifier batch rollback deletes pre-existing items — `classifier-api/main.py:458-469`

On failure, rollback does `collection.delete(ids=batch_ids)` using ALL request IDs, not just newly-added ones. Pre-existing items get deleted.

**Fix:** Track which IDs were actually added and only roll back those.

---

#### W-14: Synchronous `os.walk` blocks async event loop — `classifier-api/main.py:506-518`

`_get_dir_size` uses synchronous I/O in an async endpoint. Stalls all concurrent requests during directory walk.

**Fix:** `await asyncio.get_event_loop().run_in_executor(None, _get_dir_size, path)`

---

#### W-15: Backend Python dependencies lack upper version bounds — `backend/requirements.txt`

All deps use only `>=` (e.g., `fastapi>=0.115.0`). A future install could pull incompatible major versions.

**Fix:** Add upper bounds: `fastapi>=0.115.0,<1.0`, `sqlalchemy>=2.0.0,<3.0`, etc.

---

#### W-16: `build-essential` left in production images — backend + classifier Dockerfiles

~200 MB of compiler toolchain remains in production images, increasing attack surface and image size.

**Fix:** Multi-stage build or `apt-get purge -y build-essential && apt-get autoremove -y`.

---

#### W-17: No resource limits on any Docker service — both compose files

On docker-ai (2-core VM), a runaway process could OOM the host.

**Fix:** Add `deploy.resources.limits.memory` to compose services.

---

#### W-18: Frontend base images unpinned — `frontend/Dockerfile`

`node:20-alpine` and `nginx:alpine` have no patch versions. Future pulls could break builds.

**Fix:** Pin to specific versions: `node:20.11-alpine`, `nginx:1.27-alpine`.

---

#### W-19: `send_prompt` error response leaks internal details — `api/llm.py:162-167`

`detail=f"LLM request failed: {e}"` may expose Ollama URLs, model names, connection details.

**Fix:** Return generic error; log details server-side.

---

### 🔵 Info

| ID | File | Finding |
|----|------|---------|
| I-1 | `api/config.py:300` | Unused import `Item` in config import replace mode |
| I-2 | Multiple files | `datetime.utcnow()` deprecated since Python 3.12 |
| I-3 | `api/config.py:298-309` | Dead code block in config import (import + warning, no action) |
| I-4 | `database.py:75-79` | `json_merge` uses manual SQL string interpolation (not exploitable, input is internal) |
| I-5 | Backend-wide | 64 unused imports (F401) and 128 unused variables (F841) flagged by ruff |
| I-6 | `frontend/src/stores/ui.ts:35` | `messageListGridColumns` always returns `'1fr 1fr'` — dead computed |
| I-7 | Frontend types + templates | Deprecated `assigned_ak` (singular) still referenced alongside `assigned_aks` (plural) |
| I-8 | `frontend/src/components/nachrichten/FeedbackPanel.vue:5,12` | Unused imports `StarIcon`, `StarIconSolid` |
| I-9 | `frontend/src/components/nachrichten/TopicList.vue` | ~120 lines of item row template duplicated 3 times |
| I-10 | All modal components | No focus trapping (HeadlessUI `Dialog` available but unused) |
| I-11 | `frontend/src/main.ts` | No global error boundary (`app.config.errorHandler`) |
| I-12 | `frontend/src/router/index.ts:66` | No fallback for missing `meta.title` — would show "undefined - Liga Hessen News" |
| I-13 | `frontend/src/views/UebersichtView.vue:224-226` | Displays deprecated `assigned_ak` instead of `assigned_aks` |
| I-14 | `frontend/src/components/ChannelFormModal.vue:187-190` | `onMounted` fetch has no error handling |
| I-15 | `frontend/src/views/SourceDetailView.vue` | Minimal stub with incomplete functionality vs `SourcesView` |
| I-16 | `classifier-api/main.py`, `classifier.py` | Three different version strings (`"2.0.0"`, `"2.1.0"`, `"2.1.0-multilabel"`) |
| I-17 | `classifier-api/requirements.txt:7` | `lightgbm` dependency may be unused (~30 MB) |
| I-18 | `classifier-api/classifier.py:211,358,628` | `print()` used instead of `logger` in 3 places |
| I-19 | `classifier-api/feature_extraction.py:221-224` | Regex patterns recompiled per keyword per classification (100+ compilations/request) |
| I-20 | `.env.example` | Outdated — references `llama3.2`, missing `CLASSIFIER_*`, `GPU1_*`, `REDIS_URL` vars |
| I-21 | npm audit | 7 frontend vulnerabilities (3 high) — fixable via `npm audit fix` |
| I-22 | `classifier-api/Dockerfile` | Python 3.11 vs backend 3.12 — undocumented reason |

## Metrics Summary

| Category | Issues | Critical | Warning | Info |
|----------|--------|----------|---------|------|
| Error Handling & Robustness | 10 | 3 | 5 | 2 |
| Performance & Efficiency | 5 | 0 | 3 | 2 |
| Dead Code & Unused | 8 | 0 | 1 | 7 |
| Code Style & Idioms | 4 | 0 | 0 | 4 |
| Concurrency | 4 | 1 | 3 | 0 |
| Testing | 0 | 0 | 0 | 0 |
| Dependencies | 4 | 0 | 2 | 2 |
| Configuration & Environment | 2 | 0 | 1 | 1 |
| Logging & Observability | 2 | 0 | 1 | 1 |
| Security | 7 | 2 | 4 | 1 |
| Architecture | 1 | 0 | 0 | 1 |
| Project Hygiene | 4 | 1 | 2 | 1 |
| **Total** | **51** | **7** | **19** | **25** |

## What's Done Well

1. **3-phase read-process-write pattern** in `llm_worker.py` — releases DB connections during long LLM calls, preventing pool exhaustion. This is a sophisticated pattern that most async Python projects get wrong. The `json_merge` atomic metadata update in the same file is well-designed.

2. **Comprehensive test suite** — 612 tests covering API endpoints, services, edge cases, and error paths. Test fixtures are well-organized with a clean `conftest.py`. The recent test overhaul brought the suite fully green.

3. **Clean TypeScript frontend** — vue-tsc reports zero type errors. Vue 3 Composition API is used consistently. Stores are well-separated by domain (items, sources, rules, ui). Keyboard shortcuts and composables show good code reuse.

4. **Robust connector architecture** — RSS, Mastodon, X scraper, Google Alerts, and other connectors follow a consistent pattern. The article extractor has a thoughtful fallback chain (httpx+trafilatura → Wayback Machine → Playwright SPA).

## Top Recommendations

1. **Fix priority mapping in `api/llm.py`** (C-1). This is a data quality bug that silently downgrades every item processed through the batch reprocess endpoint. Copy the mapping from `llm_worker.py`. ~5 minutes.

2. **Fix scheduler job ID mismatch** (C-2). The PUT `/scheduler/interval` endpoint is completely broken — it always returns 404. Change `"fetch_all_sources"` to `"fetch_due_channels"`. ~2 minutes.

3. **Use `json_merge` in reprocess endpoints** (C-3). The concurrent-safe pattern already exists in `llm_worker.py`. Apply the same approach to `api/items.py:1330` and `api/llm.py:382-384`. ~30 minutes.

4. **Add non-root users to Dockerfiles** (C-5) and **remove `build-essential`** (W-16). Both are straightforward 2-3 line changes per Dockerfile that significantly reduce security exposure and image size. ~20 minutes.

5. **Run `npm audit fix`** (I-21) and **pin frontend base images** (W-18). Quick wins for dependency health. ~10 minutes.
