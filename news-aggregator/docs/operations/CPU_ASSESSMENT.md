# CPU Usage Assessment - docker-ai (pve1)

**Date:** 2026-02-10
**Host:** docker-ai (Proxmox VM, 2 vCPUs, 6GB RAM)

## Current State

The `liga-news-backend` container uses **107% CPU** (of 200% available on 2 cores).

### Process Breakdown

| Process | CPU% | Notes |
|---------|------|-------|
| Chromium renderers (4 tabs) | ~103% | Playwright headless Chrome for X.com |
| Gunicorn master + Playwright node driver | ~39% | Python app + browser orchestration |
| Chromium browser/GPU/network processes | ~25% | Supporting processes per browser instance |
| Gunicorn workers (4x) | ~4% | Actual API serving |

**Root cause: Playwright/Chromium** accounts for ~130% of the 107% total (processes overlap in measurement). The Python app itself is lightweight.

## Why It's So Heavy

### X.com Scraping Volume

- **67 X.com channels** scraped every **30 minutes**
- = **134 browser scrapes/hour** (one every ~27 seconds, non-stop)
- Each scrape: launch tab → load x.com page → scroll → extract tweets → follow links
- Concurrency: 2 simultaneous browser instances (scheduler semaphore)

### Proxy Failures Double the Work

- **0 HTTPS proxies available** (minimum required: 5)
- Every X.com scrape first **fails** with proxy (`ERR_TUNNEL_CONNECTION_FAILED`), then **retries** without proxy
- **886 tunnel failures in 24h** — effectively doubling scraping CPU cost
- 25 HTTP proxies work fine, but X.com requires HTTPS

### Compounding Factors

- **4 gunicorn workers** on 2-core VM (over-provisioned)
- **Browser pool max: 4** (can't efficiently run 4 browsers on 2 cores)
- **316 RSS channel timeouts** (60s each) — hanging connections consume resources
- Bertelsmann Stiftung RSS feeds consistently returning 500 (3 channels dead)

## Configuration Reference

| Setting | Current Value | Location |
|---------|---------------|----------|
| Scheduler fetch check | Every 1 min | scheduler.py:763 |
| x_scraper fetch interval | 30 min | DB: channels.fetch_interval_minutes |
| x_scraper concurrency | 2 | scheduler.py:23 |
| x_scraper timeout | 300s (5 min) | scheduler.py:41 |
| RSS concurrency | 10 | scheduler.py:28 |
| RSS timeout | 60s | scheduler.py:46 |
| Max browser instances | 4 | browser_pool.py:241 |
| Gunicorn workers | 4 | docker-compose.prod.yml (CMD) |
| Proxy validation interval | 300s (5 min) | proxy_manager.py:66 |
| Proxy batch size | 100 | proxy_manager.py:65 |
| Proxy refresh | Every 30 min | scheduler.py:794 |

## Channel Counts

| Connector | Channels | Interval | Browser? |
|-----------|----------|----------|----------|
| rss | 83 | 30-120 min | No |
| x_scraper | 67 | 30 min | Yes (Playwright) |
| instagram_scraper | 16 | 60 min | Yes (Playwright) |
| linkedin | 9 | 30 min | Yes (Playwright) |
| mastodon | 4 | 30-60 min | No |
| telegram | 2 | 60 min | No |
| bluesky | 1 | 60 min | No |

## Potential Optimizations (Incremental)

| Change | Expected Impact | Risk |
|--------|----------------|------|
| x_scraper interval: 30 → 120 min | **-75% browser load** | Low — politicians don't tweet every 30min |
| Gunicorn workers: 4 → 2 | -50% Python overhead | Low — matches core count |
| Browser pool: 4 → 2 | Prevents overcommit | Low |
| x_scraper concurrency: 2 → 1 | Halves simultaneous browsers | Medium — slower cycle |
| Proxy validation: 5min → 15min | Less testing overhead | Low |
| Disable dead Bertelsmann channels | Removes 3 timeout sources | None |

## Proxy Pool Status

- HTTP proxies: 25 (min required: 20) — healthy
- HTTPS proxies: 0 (min required: 5) — **broken**
- Source URL for proxy list was 404 (fixed in commit 8493190 but may need fresh deploy)
- Without HTTPS proxies, X.com scraping always fails on first attempt
