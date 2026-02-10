# X.com HTTP Scraper — PoC Results

**Date:** 2026-02-10
**Branch:** `feat/rust-x-scraper`
**Goal:** Replace Playwright-based X.com scraping (~130% CPU) with lightweight HTTP API calls (~0% CPU)

## Current Architecture (Playwright)

```
Playwright launches Chromium
    → Navigates to x.com/{username} (unauthenticated on production)
        → Waits for JS to render tweets
            → Scrolls page, waits more
                → Extracts DOM elements
```

**Cost:** ~130% CPU on a 2-core VM for 67 accounts every 30 minutes.
**Note:** Production runs unauthenticated — `data/` dir is in `.dockerignore`, no cookie file in Docker.

## PoC Results

### Approach 1: Syndication API (No Auth)

**Endpoint:** `GET https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}`

| What | Result |
|------|--------|
| Auth required | No |
| Returns | Structured JSON in `__NEXT_DATA__` |
| Tweet count | Up to 100 per request |
| Data freshness | **Stale** — weeks to months old |
| Content type | "Highlights" (popular), NOT chronological |
| Account coverage | Partial — some accounts return 0 entries |
| Response time | ~200ms |

**Test results:**

| Account | Entries | Latest Tweet | Staleness |
|---------|---------|-------------|-----------|
| @Boris_Rhein | 100 | 2025-06-02 | 253 days |
| @faznet | 100 | 2025-11-10 | 92 days |
| @ProAsyl | 100 | 2023-06-08 | 977 days |
| @HNA_online | 0 | N/A | N/A |
| @tabormagazin | 0 | N/A | N/A |

**Verdict:** Unreliable for news aggregation. Data too stale, not all accounts indexed.

**Useful for:** Discovering user IDs (returned in tweet data) for use with other approaches.

### Approach 2: Guest Token + GraphQL (No Auth)

**Steps:** Activate guest token → Extract query IDs from JS bundle → UserByScreenName → UserTweets

| Step | Result |
|------|--------|
| Guest token activation | Works (200 OK) |
| Query ID extraction | Works (found UserTweets + UserByScreenName) |
| UserByScreenName | **404** — blocked for guest tokens since 2024 |
| UserTweets (with known ID) | 200 but **empty timeline** (`TimelineTerminateTimeline`) |

**Verdict:** Completely blocked without authentication. X.com progressively restricted guest token access in 2024-2025.

**Feature flags note:** X.com now requires ~35 feature flags including Grok-related ones (as of late 2025). Missing any flag results in 400 error. These change periodically.

### Approach 3: Authenticated GraphQL (Needs Cookies)

**Requirements:** Valid `auth_token` + `ct0` cookies from a logged-in X.com session.

**Status:** NOT TESTED — no valid cookies available. Production runs unauthenticated (Playwright renders JS instead).

**Expected performance if working:**
- ~100ms per account (vs ~30s with Playwright)
- ~0% CPU (vs ~130%)
- Structured JSON response, no DOM parsing

**Risks:**
- Cookie expiration (need monitoring + alerting)
- query_id rotation every 2-4 weeks (need auto-extraction)
- Feature flag changes (need periodic updates)
- Account suspension risk

## Decision Matrix

| Approach | Auth Needed | Reliability | Freshness | CPU Savings |
|----------|-------------|-------------|-----------|-------------|
| Syndication API | None | Low | Stale | 99% |
| Guest + GraphQL | None | None | N/A | N/A |
| Auth + GraphQL | Cookies | Medium | Real-time | 99% |
| Playwright (current) | None | High | Real-time | Baseline |

## Recommended Path Forward

### 1. Quick Win (Immediate, No Code Changes)

Reduce X.com scraping frequency in production:
- 67 accounts × every 30 min = 134 browser scrapes/hour
- 67 accounts × every 120 min = 33.5 browser scrapes/hour → **-75% CPU**

Politicians don't tweet every 30 minutes. 2-hour intervals are sufficient for news aggregation.

### 2. Medium-Term (If Cookies Available)

If the user can provide valid, non-obfuscated cookies:
1. Build `x_api` connector alongside `x_scraper`
2. Authenticated GraphQL for latest tweets (~100ms/account)
3. Playwright as automatic fallback on auth failure
4. Alert immediately when cookies expire

### 3. Playwright Optimization (Alternative to API)

Instead of replacing Playwright:
- Reduce `MAX_BROWSER_INSTANCES` from 4 to 2
- Reduce gunicorn workers from 4 to 2 (match cores)
- Fix HTTPS proxy pool (currently 0 proxies → every request fails + retries)
- Reduce proxy validation frequency (5min → 15min)

## Files

- **PoC script:** `poc/x_api_poc.py` — tests all three approaches
- **CPU assessment:** `docs/operations/CPU_ASSESSMENT.md`
- **Python venv:** `.poc-venv/` (httpx, twitter-api-client installed)

## Sources

- [twitter-api-client](https://github.com/trevorhobenshield/twitter-api-client) — Python GraphQL implementation
- [snscrape #995](https://github.com/JustAnotherArchivist/snscrape/issues/995) — Documents guest token restrictions
- [ScrapFly: How to Scrape X.com 2026](https://scrapfly.io/blog/posts/how-to-scrape-twitter) — Current scraping landscape
- [X.com Home Timeline API Design](https://trekhleb.dev/blog/2024/api-design-x-home-timeline/)
