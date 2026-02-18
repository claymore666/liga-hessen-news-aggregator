# CPU Assessment — docker-ai Production

Last updated: 2026-02-11

## Environment

- **Host**: docker-ai (192.168.0.124)
- **CPU cores**: 2
- **Containers**: backend (4 Gunicorn workers), PostgreSQL, Redis, frontend

## Problem

Backend container sustained **~175% CPU** on a 2-core machine, nearly maxing out both cores.

## Root Cause Analysis

### Channel Inventory (182 active channels)

| Connector Type | Channels | Interval | Uses Playwright | Concurrency Limit |
|---|---|---|---|---|
| rss | 83 | 30-120 min | No | 10 |
| x_scraper | 67 | 30 min | **Yes** | 2 |
| instagram_scraper | 16 | 60 min | **Yes** | 2 |
| linkedin | 9 | 30 min | **Yes** | 2 |
| mastodon | 4 | 30-60 min | No | 5 |
| telegram | 2 | 60 min | No | 3 |
| bluesky | 1 | 60 min | No | 5 |

### CPU Breakdown by Factor

| Factor | CPU Impact | Evidence |
|---|---|---|
| **67 X.com channels @ 30min via Playwright** | Dominant (~60%) | 647 log lines/hour, 97 Playwright article extractions/hour |
| **16 Instagram channels @ 60min via Playwright** | High (~15%) | 32 log lines/hour |
| **9 LinkedIn channels @ 30min via Playwright** | Moderate (~10%) | 14 log lines/hour |
| **Scheduler overruns** | Wasted CPU | 50 skips/hour — fetch cycles can't finish before next 1-min tick |
| **Proxy timeouts and retries** | Wasted CPU | 66 errors/hour from ERR_TIMED_OUT and proxy failures |
| **83 RSS channels** | Low (~5%) | Lightweight HTTP + XML parsing |

### Why X.com Dominates

Each X.com fetch:
1. Launches a Playwright Chromium browser
2. Loads the X.com profile page with JS rendering
3. Scrolls to load tweets (3 cycles)
4. For each tweet with links: opens **another Playwright page** to extract the article
5. Up to 20 tweets × 1 link each = 20 additional Playwright page loads per channel

With 67 channels at 30min intervals, the system processes ~134 X.com fetches/hour plus hundreds of link-following page loads.

## Changes Applied (2026-02-11)

### 1. X.com scraper interval: 30 → 120 min

**Rationale**: Politicians don't tweet every 30 minutes. 2-hour intervals provide adequate coverage with 75% less browser load.

**Implementation**: Database UPDATE on production channels (67 channels affected).

### 2. Browser pool: 4 → 2 concurrent browsers

**Rationale**: docker-ai has 2 CPU cores. Running 4 concurrent browsers causes excessive context switching.

**Implementation**: `BROWSER_POOL_MAX` environment variable (default: 2), configurable in docker-compose.

**Config**: `docker-compose.prod.yml` → `BROWSER_POOL_MAX=2`

### Expected Impact

| Metric | Before | After (estimated) |
|---|---|---|
| X.com fetches/hour | ~134 | ~34 |
| Playwright pages/hour | ~230+ | ~60 |
| Peak concurrent browsers | 4 | 2 |
| Backend CPU | ~175% | ~40-60% |
| Scheduler overruns | 50/hour | ~0 |

## Future Optimizations (Not Yet Implemented)

| # | Fix | CPU Savings | Notes |
|---|---|---|---|
| 1 | Reduce Gunicorn workers: 4 → 2 | Context switching | Only if CPU still high after browser changes |
| 2 | LinkedIn interval: 30 → 120 min | Moderate | Same rationale as X.com |
| 3 | Disable Playwright link-following for X.com | Major | Switch to httpx+trafilatura for article extraction |
| 4 | HTTP-only X.com scraping | Eliminates browser | Blocked: requires authentication (see PoC in feat/rust-x-scraper) |

## Monitoring

```bash
# Current CPU usage
ssh docker-ai "docker stats --no-stream liga-news-backend"

# Scheduler overruns (should be 0)
ssh docker-ai "docker logs liga-news-backend --since 60m 2>&1 | grep 'skipped: maximum number' | wc -l"

# Playwright activity
ssh docker-ai "docker logs liga-news-backend --since 60m 2>&1 | grep -c 'Extracted article via Playwright'"

# Errors
ssh docker-ai "docker logs liga-news-backend --since 60m 2>&1 | grep -c 'ERR_TIMED_OUT\|proxy failed'"
```
