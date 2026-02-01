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

- **Max browsers**: 4 concurrent (configurable via `max_browsers`)
- Uses `asyncio.Semaphore` to limit concurrent browser launches
- Each `get_browser()` call launches a fresh Chromium instance and closes it on exit

### Error Recovery

The pool uses a **generation-based restart** mechanism:

1. Each browser error increments an error counter
2. After `error_threshold` (default 10) errors, the Playwright driver restarts
3. A **generation counter** prevents redundant restarts when multiple callers fail simultaneously
4. A **cooldown** (30s) prevents restart storms
5. After 3 consecutive restart failures, the pool backs off until cooldown expires

### Lifecycle

- **Initialization**: Lazy — Playwright starts on first `get_browser()` call
- **Shutdown**: `browser_pool.shutdown()` is called during backend shutdown
- **Health check**: `browser_pool.health_check()` returns generation, error count, and state

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_browsers` | 4 | Max concurrent Chromium instances |
| `error_threshold` | 10 | Errors before driver restart |
| `RESTART_COOLDOWN` | 30s | Min time between restart attempts |
| `MAX_RESTART_FAILURES` | 3 | Consecutive failures before backing off |

## Troubleshooting

See [TROUBLESHOOTING.md](../operations/TROUBLESHOOTING.md#browser-pool--playwright-issues) for common issues and fixes.
