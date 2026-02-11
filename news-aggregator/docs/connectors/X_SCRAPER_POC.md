# X.com HTTP Scraper PoC

Date: 2026-02-10
Branch: `feat/rust-x-scraper`
Status: **Concluded** — no viable unauthenticated HTTP replacement for Playwright

## Motivation

The `x_scraper` connector uses Playwright (headless Chromium) to scrape X.com profiles. With 67 X.com channels fetching every 30 minutes, this was the dominant CPU consumer on docker-ai (~130% CPU on a 2-core machine).

Goal: Replace Playwright with lightweight HTTP requests to eliminate browser overhead.

## Approaches Tested

### 1. Guest Token + GraphQL API

**Method**: Obtain a guest token via `https://api.x.com/1.1/guest/activate.json`, then call GraphQL endpoints (`UserByScreenName`, `UserTweets`).

**Result**: Blocked.
- `UserByScreenName` returns 404
- `UserTweets` returns empty timeline
- X.com has progressively locked down guest token access since 2024

### 2. Syndication API

**Method**: Fetch `https://syndication.twitter.com/srv/timeline-profile/screen-name/{user}`, parse `__NEXT_DATA__` JSON from the response.

**Result**: Works, but returns stale data.
- Returns up to 100 tweets per request
- Content is "highlights" curated by X.com's algorithm — not chronological
- Tweets can be weeks or months old
- Not suitable for a news aggregator that needs recent posts

### 3. Authenticated GraphQL

**Method**: Use `auth_token` + `ct0` cookies from a logged-in session to call the GraphQL API.

**Result**: Works — the only method that returns chronological latest tweets.
- Requires manually exporting cookies from a browser session
- Cookies expire and need periodic renewal
- ToS risk: X.com prohibits automated access with user credentials
- Feature flags list has grown to ~35 entries (including Grok-related ones)

## Decision

No viable unauthenticated HTTP replacement exists. Instead, we optimized the existing Playwright-based approach:

1. **Increased fetch interval**: 30 min → 120 min (politicians don't tweet that frequently)
2. **Reduced browser pool**: 4 → 2 concurrent browsers (matches CPU cores)

These changes reduced CPU from ~175% to ~4% on docker-ai. See [CPU_ASSESSMENT.md](../operations/CPU_ASSESSMENT.md).

## Future Options

| Option | Viability | Notes |
|---|---|---|
| Nitter instances (`twitter` connector) | Low | Most public instances are blocked or shut down |
| Authenticated cookies | Medium | Works but fragile, requires manual cookie renewal, ToS risk |
| Official X API (Basic tier) | Medium | $100/month, limited to 10k reads/month (may not cover 67 accounts) |
| Syndication API for non-time-critical use | Medium | Could supplement if stale highlights are acceptable |
| Drop X.com entirely | Viable | If cost/complexity outweighs value of X.com monitoring |
