# Browser Pool

Shared Playwright instance management for browser-based scrapers and article extraction.

## Purpose

Multiple components need headless Chromium browsers:
- `x_scraper.py` — Twitter/X profile scraping
- `instagram_scraper.py` — Instagram post scraping
- `article_extractor.py` — SPA fallback for JavaScript-rendered pages

Without pooling, each scraper spawns its own Playwright node driver process, consuming significant resources and causing "Resource temporarily unavailable" (Errno 11) errors.

## Architecture

### Singleton Pattern

`browser_pool` is a module-level singleton (`BrowserPool` instance) shared across the entire backend process.

```python
from services.browser_pool import browser_pool

async with browser_pool.get_browser() as browser:
    context = await browser.new_context(...)
    page = await context.new_page()
    # ... use page ...
    await context.close()
```

### Concurrency Control

- **Max browsers**: 2 concurrent (configurable via `BROWSER_POOL_MAX` env var, default: 2)
- Uses `asyncio.Semaphore` to limit concurrent browser launches
- Each `get_browser()` call launches a fresh Chromium instance and closes it on exit
- Set to 2 to match docker-ai's 2-core CPU (see [CPU_ASSESSMENT.md](../operations/CPU_ASSESSMENT.md))

### Error Recovery

The pool uses a **generation-based restart** mechanism:

1. Each browser error increments an error counter
2. After `error_threshold` (default 10) errors, the Playwright driver restarts
3. A **generation counter** prevents redundant restarts when multiple callers fail simultaneously
4. A **cooldown** (30s) prevents restart storms
5. After 3 consecutive restart failures, the pool backs off until cooldown expires

### Bounded Cleanup and Hard Reset (#187)

Playwright sends `page.close()`, `context.close()` and `browser.close()` with an
**infinite** protocol timeout. A Chromium page that refuses to close never answers,
and a task cancelled while awaiting such a call also hangs forever (the Playwright
client reacts to `CancelledError` by waiting for the reply). In September 2026 this
wedged the production scheduler for two days while every container reported healthy.

Rules that every consumer of the pool now follows:

- **Close through `close_quietly(obj, what, timeout=None)`.** It runs the close in
  its own task and waits at most `BROWSER_CLOSE_TIMEOUT_SECONDS` (default 10). A
  close that does not finish is *abandoned*, never cancelled, and the function
  returns `False`. Cancelling the caller lands in `asyncio.wait`, not inside the
  Playwright call, so scheduler timeouts can always unwind.
- **Contexts set default timeouts** (`set_default_timeout(15000)`,
  `set_default_navigation_timeout(45000)`) so no single action can consume the
  channel's whole fetch budget.
- **`browser_pool.hard_reset(reason)`** is the escape hatch. It SIGKILLs the node
  driver process (every pending protocol call fails immediately with
  "Connection closed", Chromium dies with its parent), reaps any orphaned headless
  Chromium, bumps the generation and installs a fresh semaphore so slots held by
  abandoned tasks are recovered. The scheduler calls it at the end of any fetch
  cycle in which a task had to be abandoned (see
  [SCHEDULER.md](SCHEDULER.md#timeouts-and-abandoned-tasks)).
- `_restart_driver()` and `shutdown()` are bounded too: if `playwright.stop()`
  hangs, the driver is killed.

`browser_pool.health_check()` (published under `browser_pool` in the scheduler
stats and shown by `GET /api/admin/health`) reports `available_slots`,
`hung_closes`, `hard_resets`, `last_hard_reset_reason`, `driver_pid` and
`driver_alive`.

### Lifecycle

- **Initialization**: Lazy — Playwright starts on first `get_browser()` call
- **Shutdown**: `browser_pool.shutdown()` is called during backend shutdown
- **Health check**: `browser_pool.health_check()` returns generation, error count, and state

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `BROWSER_POOL_MAX` (env) | 2 | Max concurrent Chromium instances |
| `BROWSER_CLOSE_TIMEOUT_SECONDS` (env) | 10 | Upper bound for one page/context/browser `close()` |
| `error_threshold` | 10 | Errors before driver restart |
| `RESTART_COOLDOWN` | 30s | Min time between restart attempts |
| `MAX_RESTART_FAILURES` | 3 | Consecutive failures before backing off |

## Troubleshooting

See [TROUBLESHOOTING.md](../operations/TROUBLESHOOTING.md#browser-pool--playwright-issues) for common issues and fixes.
