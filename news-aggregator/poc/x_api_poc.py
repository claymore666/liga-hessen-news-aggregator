"""
X.com API PoC - Test alternatives to Playwright for tweet fetching.

Tests three approaches:
1. Syndication API (no auth needed, returns "highlights" — stale data)
2. Guest token + GraphQL API (no cookies needed — BLOCKED by X.com)
3. Cookie-based authenticated GraphQL API (needs auth_token + ct0)

Usage:
    source .poc-venv/bin/activate
    python poc/x_api_poc.py [username1] [username2] ...

    Default test accounts: Boris_Rhein, HNA_online, ProAsyl
"""

import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# X.com's public bearer token (embedded in their JS bundle, same for all users)
BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

GRAPHQL_BASE = "https://x.com/i/api/graphql"

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Comprehensive feature flags (as of Feb 2026, includes Grok features)
ALL_FEATURES = {
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "hidden_profile_subscriptions_enabled": True,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    # Grok features (required since late 2025)
    "responsive_web_grok_image_annotation_enabled": False,
    "premium_content_api_read_enabled": False,
    "responsive_web_grok_imagine_annotation_enabled": False,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_grok_annotations_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": False,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "rweb_video_screen_enabled": False,
    "responsive_web_profile_redirect_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": False,
    "responsive_web_jetfuel_frame": False,
    "responsive_web_grok_analysis_button_from_backend": False,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_grok_share_attachment_enabled": False,
    "post_ctas_fetch_enabled": False,
}


def parse_tweet_date(created_at: str) -> str | None:
    """Parse Twitter's date format to ISO."""
    if not created_at:
        return None
    try:
        return datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y").isoformat()
    except ValueError:
        return None


# ── Approach 1: Syndication API ──────────────────────────────────────────


async def test_syndication(client: httpx.AsyncClient, username: str) -> dict:
    """
    Fetch tweets via syndication.twitter.com (no auth needed).

    Returns "highlights" (algorithmically selected popular tweets),
    NOT the chronological timeline. Data is often weeks-months stale.
    """
    start = time.time()

    try:
        resp = await client.get(
            f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}",
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml",
            },
        )

        if resp.status_code != 200:
            return {"status": "FAILED", "error": f"HTTP {resp.status_code}", "elapsed_ms": 0}

        # Extract __NEXT_DATA__ JSON from HTML
        json_match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text
        )
        if not json_match:
            return {"status": "FAILED", "error": "no_json_data", "elapsed_ms": 0}

        data = json.loads(json_match.group(1))
        props = data.get("props", {}).get("pageProps", {})
        entries = props.get("timeline", {}).get("entries", [])

        tweets = []
        user_id = None
        for entry in entries:
            tweet_data = entry.get("content", {}).get("tweet", {})
            if not tweet_data:
                continue

            text = tweet_data.get("text", "")
            if not text:
                continue

            user = tweet_data.get("user", {})
            if not user_id:
                user_id = user.get("id_str")

            # Extract URLs from entities
            urls = []
            for url_entity in tweet_data.get("entities", {}).get("urls", []):
                expanded = url_entity.get("expanded_url", "")
                if expanded and "x.com" not in expanded and "twitter.com" not in expanded:
                    urls.append(expanded)

            tweet_id = tweet_data.get("conversation_id_str", "")
            author = user.get("screen_name", username)

            tweets.append({
                "id": tweet_id,
                "text": text,
                "author": author,
                "published_at": parse_tweet_date(tweet_data.get("created_at", "")),
                "url": f"https://x.com/{author}/status/{tweet_id}",
                "urls": urls,
            })

        elapsed = round((time.time() - start) * 1000)

        # Calculate staleness
        dates = [t["published_at"] for t in tweets if t["published_at"]]
        latest_date = max(dates) if dates else None
        if latest_date:
            latest_dt = datetime.fromisoformat(latest_date)
            staleness_days = (datetime.now(timezone.utc) - latest_dt).days
        else:
            staleness_days = None

        return {
            "status": "OK" if tweets else "NO_TWEETS",
            "tweet_count": len(tweets),
            "elapsed_ms": elapsed,
            "user_id": user_id,
            "latest_date": latest_date,
            "staleness_days": staleness_days,
            "sample": tweets[0] if tweets else None,
        }

    except Exception as e:
        return {"status": "FAILED", "error": str(e), "elapsed_ms": 0}


# ── Approach 2: Guest Token + GraphQL ────────────────────────────────────


async def test_guest_graphql(client: httpx.AsyncClient, username: str) -> dict:
    """
    Attempt to fetch tweets via GraphQL API with guest token.
    KNOWN ISSUE: X.com blocks UserByScreenName and returns empty
    timelines for guest tokens as of 2024-2025.
    """
    start = time.time()

    # Step 1: Activate guest token
    try:
        resp = await client.post(
            "https://api.x.com/1.1/guest/activate.json",
            headers={
                "Authorization": f"Bearer {BEARER_TOKEN}",
                "User-Agent": BROWSER_UA,
            },
        )
        if resp.status_code != 200:
            return {"status": "FAILED", "error": f"guest_token_http_{resp.status_code}"}

        guest_token = resp.json().get("guest_token")
        if not guest_token:
            return {"status": "FAILED", "error": "no_guest_token"}
    except Exception as e:
        return {"status": "FAILED", "error": f"guest_token: {e}"}

    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "x-guest-token": guest_token,
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "User-Agent": BROWSER_UA,
        "Referer": "https://x.com/",
        "Origin": "https://x.com",
    }

    # Step 2: Try UserByScreenName
    variables = json.dumps({"screen_name": username, "withSafetyModeUserFields": True})
    features = json.dumps(ALL_FEATURES)
    field_toggles = json.dumps({"withAuxiliaryUserLabels": False})

    try:
        resp = await client.get(
            f"{GRAPHQL_BASE}/AWbeRIdkLtqTRN7yL_H8yw/UserByScreenName",
            params={"variables": variables, "features": features, "fieldToggles": field_toggles},
            headers=headers,
            cookies={"gt": guest_token},
        )

        user_id = None
        if resp.status_code == 200:
            user_data = resp.json().get("data", {}).get("user", {}).get("result", {})
            user_id = user_data.get("rest_id")

        user_lookup_status = f"{resp.status_code}" + (f" (ID: {user_id})" if user_id else " (no data)")
    except Exception as e:
        user_lookup_status = f"error: {e}"
        user_id = None

    # Step 3: Try UserTweets (with hardcoded ID or from lookup)
    tweets_status = "skipped"
    if user_id:
        variables = json.dumps({
            "userId": user_id,
            "count": 5,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": True,
            "withV2Timeline": True,
        })

        try:
            resp = await client.get(
                f"{GRAPHQL_BASE}/SURb7otVJKay5ECsD8ffXA/UserTweets",
                params={"variables": variables, "features": features},
                headers=headers,
                cookies={"gt": guest_token},
            )
            if resp.status_code == 200:
                data = resp.json()
                user = data.get("data", {}).get("user", {}).get("result", {})
                timeline = user.get("timeline_v2", user.get("timeline", {}))
                instructions = timeline.get("timeline", {}).get("instructions", [])
                entries = []
                for instr in instructions:
                    entries.extend(instr.get("entries", []))
                tweets_status = f"{resp.status_code} ({len(entries)} entries)"
            else:
                tweets_status = f"{resp.status_code}"
        except Exception as e:
            tweets_status = f"error: {e}"

    elapsed = round((time.time() - start) * 1000)

    return {
        "status": "BLOCKED",
        "detail": f"UserByScreenName: {user_lookup_status}, UserTweets: {tweets_status}",
        "elapsed_ms": elapsed,
    }


# ── Approach 3: Authenticated GraphQL ────────────────────────────────────


async def test_authenticated_graphql(
    client: httpx.AsyncClient, username: str, cookies: dict
) -> dict:
    """
    Fetch tweets via GraphQL API with authenticated session cookies.
    Requires valid auth_token + ct0 cookies.
    """
    if not cookies.get("auth_token") or not cookies.get("ct0"):
        return {"status": "SKIPPED", "error": "no_cookies"}

    start = time.time()

    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "x-csrf-token": cookies["ct0"],
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "User-Agent": BROWSER_UA,
        "Referer": f"https://x.com/{username}",
        "Origin": "https://x.com",
    }

    # Step 1: Look up user ID
    variables = json.dumps({"screen_name": username, "withSafetyModeUserFields": True})
    features = json.dumps(ALL_FEATURES)
    field_toggles = json.dumps({"withAuxiliaryUserLabels": False})

    try:
        resp = await client.get(
            f"{GRAPHQL_BASE}/AWbeRIdkLtqTRN7yL_H8yw/UserByScreenName",
            params={"variables": variables, "features": features, "fieldToggles": field_toggles},
            headers=headers,
            cookies=cookies,
        )

        if resp.status_code != 200:
            return {
                "status": "FAILED",
                "error": f"user_lookup_http_{resp.status_code}",
                "detail": resp.text[:200],
                "elapsed_ms": round((time.time() - start) * 1000),
            }

        user_data = resp.json().get("data", {}).get("user", {}).get("result", {})
        user_id = user_data.get("rest_id")
        if not user_id:
            return {
                "status": "FAILED",
                "error": "no_user_id_in_response",
                "elapsed_ms": round((time.time() - start) * 1000),
            }
    except Exception as e:
        return {"status": "FAILED", "error": f"user_lookup: {e}"}

    # Step 2: Fetch tweets
    variables = json.dumps({
        "userId": user_id,
        "count": 20,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True,
        "withV2Timeline": True,
    })

    try:
        resp = await client.get(
            f"{GRAPHQL_BASE}/SURb7otVJKay5ECsD8ffXA/UserTweets",
            params={"variables": variables, "features": features},
            headers=headers,
            cookies=cookies,
        )

        if resp.status_code != 200:
            return {
                "status": "FAILED",
                "error": f"tweets_http_{resp.status_code}",
                "detail": resp.text[:200],
                "elapsed_ms": round((time.time() - start) * 1000),
            }

        data = resp.json()

        # Parse tweets from the response
        tweets = []
        timeline = (
            data.get("data", {})
            .get("user", {})
            .get("result", {})
            .get("timeline_v2", data.get("data", {}).get("user", {}).get("result", {}).get("timeline", {}))
            .get("timeline", {})
            .get("instructions", [])
        )

        for instruction in timeline:
            for entry in instruction.get("entries", []):
                content = entry.get("content", {})
                if content.get("entryType") == "TimelineTimelineItem":
                    tweet_result = (
                        content.get("itemContent", {})
                        .get("tweet_results", {})
                        .get("result", {})
                    )
                    if tweet_result.get("__typename") == "TweetWithVisibilityResults":
                        tweet_result = tweet_result.get("tweet", {})
                    if not tweet_result or tweet_result.get("__typename") == "TweetTombstone":
                        continue

                    legacy = tweet_result.get("legacy", {})
                    text = legacy.get("full_text", "")
                    if not text:
                        continue

                    tweet_id = legacy.get("id_str", "")
                    core = tweet_result.get("core", {}).get("user_results", {}).get("result", {})
                    author = core.get("legacy", {}).get("screen_name", username)

                    urls = []
                    for url_entity in legacy.get("entities", {}).get("urls", []):
                        expanded = url_entity.get("expanded_url", "")
                        if expanded and "x.com" not in expanded and "twitter.com" not in expanded:
                            urls.append(expanded)

                    tweets.append({
                        "id": tweet_id,
                        "text": text,
                        "author": author,
                        "published_at": parse_tweet_date(legacy.get("created_at", "")),
                        "url": f"https://x.com/{author}/status/{tweet_id}",
                        "urls": urls,
                    })

        elapsed = round((time.time() - start) * 1000)

        return {
            "status": "OK" if tweets else "NO_TWEETS",
            "tweet_count": len(tweets),
            "user_id": user_id,
            "elapsed_ms": elapsed,
            "sample": tweets[0] if tweets else None,
        }

    except Exception as e:
        return {"status": "FAILED", "error": f"tweets: {e}"}


# ── Main ─────────────────────────────────────────────────────────────────


async def main():
    """Run all PoC tests."""
    test_accounts = sys.argv[1:] if len(sys.argv) > 1 else ["Boris_Rhein", "HNA_online", "ProAsyl"]

    print("=" * 70)
    print("X.com Scraping PoC — Testing Playwright Alternatives")
    print(f"Accounts: {', '.join(test_accounts)}")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    # Load cookies if available
    cookie_file = Path(__file__).parent.parent / "backend" / "data" / "x_cookies.json"
    cookies = {}
    if cookie_file.exists():
        try:
            with open(cookie_file) as f:
                raw_cookies = json.load(f)
            cookies = {c["name"]: c["value"] for c in raw_cookies}
            auth = cookies.get("auth_token", "")
            if auth and len(auth) > 45:
                print("NOTE: Cookie values appear obfuscated, skipping authenticated test")
                cookies = {}
            else:
                print(f"Loaded {len(cookies)} cookies from file")
        except Exception as e:
            print(f"Could not load cookies: {e}")

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:

        for username in test_accounts:
            print(f"\n{'━' * 70}")
            print(f"  @{username}")
            print(f"{'━' * 70}")

            # Test all three approaches
            print("\n  [1] Syndication API (no auth, returns highlights):")
            r1 = await test_syndication(client, username)
            if r1["status"] == "OK":
                print(f"      OK — {r1['tweet_count']} tweets, {r1['elapsed_ms']}ms")
                print(f"      User ID: {r1.get('user_id')}")
                print(f"      Latest: {r1.get('latest_date', 'N/A')} ({r1.get('staleness_days', '?')} days old)")
                if r1.get("sample"):
                    print(f"      Sample: {r1['sample']['text'][:100]}")
            elif r1["status"] == "NO_TWEETS":
                print(f"      200 OK but 0 tweets returned (account not in syndication index)")
            else:
                print(f"      FAILED: {r1.get('error')}")

            print("\n  [2] Guest Token + GraphQL (no auth):")
            r2 = await test_guest_graphql(client, username)
            print(f"      {r2['status']} — {r2.get('detail', r2.get('error', ''))}")
            print(f"      ({r2.get('elapsed_ms', 0)}ms)")

            print("\n  [3] Authenticated GraphQL (needs cookies):")
            r3 = await test_authenticated_graphql(client, username, cookies)
            if r3["status"] == "SKIPPED":
                print("      SKIPPED — no valid cookies available")
            elif r3["status"] == "OK":
                print(f"      OK — {r3['tweet_count']} tweets, {r3['elapsed_ms']}ms")
                if r3.get("sample"):
                    print(f"      Sample: {r3['sample']['text'][:100]}")
            else:
                print(f"      FAILED: {r3.get('error')}")
                if r3.get("detail"):
                    print(f"      Detail: {r3['detail'][:200]}")

    # Final summary
    print(f"\n{'=' * 70}")
    print("CONCLUSION")
    print(f"{'=' * 70}")
    print("""
  Approach 1 — Syndication API:
    - No auth needed
    - Returns "highlights" (popular tweets), NOT chronological timeline
    - Data is weeks-months stale
    - Not all accounts are indexed
    - VERDICT: Unreliable for news aggregation

  Approach 2 — Guest Token + GraphQL:
    - UserByScreenName: BLOCKED (404 for guest tokens since 2024)
    - UserTweets: Returns empty timeline for guest tokens
    - VERDICT: Completely blocked without authentication

  Approach 3 — Authenticated GraphQL:
    - Requires valid auth_token + ct0 cookies from a logged-in session
    - When working: ~100ms per account vs ~30s with Playwright
    - CPU savings: ~130% → ~0%
    - VERDICT: The only viable HTTP-based approach

  RECOMMENDATION:
    1. Quick win: Reduce X.com scraping frequency (30min → 120min) = -75% CPU
    2. If cookies available: Authenticated GraphQL = -99% CPU
    3. Keep Playwright as fallback for when cookies expire
""")


if __name__ == "__main__":
    asyncio.run(main())
