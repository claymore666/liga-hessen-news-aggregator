#!/usr/bin/env python3
"""
X.com Authentication Helper

Opens a browser for manual OAuth login, then saves cookies for automated scraping.
Run this script when cookies expire or for initial setup.

Usage:
    python scripts/x_auth.py
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Cookie storage paths
# Local path (for running on host)
LOCAL_COOKIE_FILE = Path(__file__).parent.parent / "data" / "x_cookies.json"
# Also save to /tmp for easy Docker copy
TMP_COOKIE_FILE = Path("/tmp/x_cookies.json")

COOKIE_FILE = LOCAL_COOKIE_FILE


async def login_and_save_cookies():
    """Open browser for manual login, then save cookies."""

    print("\n" + "=" * 60)
    print("X.com Authentication Helper")
    print("=" * 60)
    print("\nA browser window will open. Please:")
    print("1. Click 'Sign in' on X.com")
    print("2. Complete your OAuth/Google login")
    print("3. Wait until you see your timeline")
    print("4. The script will automatically detect login and save cookies")
    print("\n" + "=" * 60 + "\n")

    input("Press Enter to open browser...")

    async with async_playwright() as p:
        # Use clean browser - simpler and more reliable
        logger.info("Launching browser...")
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="de-DE",
        )
        page = await context.new_page()

        # Navigate to X.com
        logger.info("Opening X.com login page...")
        await page.goto("https://x.com/login", wait_until="domcontentloaded", timeout=30000)

        # Wait for user to complete login
        print("\n" + "-" * 50)
        print("INSTRUCTIONS:")
        print("1. Log in to X.com in the browser window")
        print("2. Make sure you can see your timeline at x.com/home")
        print("3. Press ENTER here when you're logged in")
        print("-" * 50 + "\n")

        input("Press ENTER when you're logged in to X.com...")

        # Give page a moment to settle
        await asyncio.sleep(2)

        logger.info("Checking for cookies...")

        # Get all cookies
        all_cookies = await context.cookies()
        logger.info(f"Total cookies in browser: {len(all_cookies)}")

        # Also try getting cookies for specific URLs
        x_url_cookies = await context.cookies(["https://x.com", "https://twitter.com"])
        logger.info(f"Cookies for x.com/twitter.com URLs: {len(x_url_cookies)}")

        # Use all cookies
        cookies = all_cookies

        # Filter for X.com cookies we need
        x_cookies = [c for c in cookies if "x.com" in c.get("domain", "") or "twitter.com" in c.get("domain", "")]
        logger.info(f"X.com/Twitter cookies found: {len(x_cookies)}")

        # Show all cookie names for debugging
        if x_cookies:
            cookie_names = [c["name"] for c in x_cookies]
            logger.info(f"Cookie names: {cookie_names[:20]}...")  # First 20
        else:
            # Show some other cookies to debug
            other_domains = set(c.get("domain", "unknown") for c in cookies)
            logger.info(f"Other cookie domains: {list(other_domains)[:10]}")

        # Find the important ones
        auth_token = next((c for c in x_cookies if c["name"] == "auth_token"), None)
        ct0 = next((c for c in x_cookies if c["name"] == "ct0"), None)

        if not auth_token:
            logger.error("auth_token cookie not found!")
            logger.info("This might mean:")
            logger.info("  - You're not logged in to X.com")
            logger.info("  - The browser profile isn't sharing cookies with Playwright")
            logger.info("  - Try logging in again in the browser window")

            retry = input("\nPress ENTER to retry cookie check, or 'q' to quit: ")
            if retry.lower() != 'q':
                all_cookies = await context.cookies()
                x_cookies = [c for c in all_cookies if "x.com" in c.get("domain", "") or "twitter.com" in c.get("domain", "")]
                auth_token = next((c for c in x_cookies if c["name"] == "auth_token"), None)
                ct0 = next((c for c in x_cookies if c["name"] == "ct0"), None)

            if not auth_token:
                await browser.close()
                return False

        logger.info(f"Found {len(x_cookies)} X.com cookies")
        logger.info(f"  auth_token: {'✓' if auth_token else '✗'}")
        logger.info(f"  ct0: {'✓' if ct0 else '✗'}")

        # Save cookies to multiple locations
        COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(COOKIE_FILE, "w") as f:
            json.dump(x_cookies, f, indent=2)
        logger.info(f"Cookies saved to {COOKIE_FILE}")

        # Also save to /tmp for easy Docker copy
        with open(TMP_COOKIE_FILE, "w") as f:
            json.dump(x_cookies, f, indent=2)
        logger.info(f"Cookies also saved to {TMP_COOKIE_FILE}")

        # Test the cookies by visiting a blocked profile
        logger.info("\nTesting cookies on a blocked profile...")
        await page.goto("https://x.com/cdu_fraktion", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Check for tweets
        tweets = await page.query_selector_all('[data-testid="tweet"]')
        if tweets:
            logger.info(f"SUCCESS! Found {len(tweets)} tweets on blocked profile!")
        else:
            logger.warning("No tweets found - cookies might not be working correctly")

        print("\n" + "=" * 60)
        print("SUCCESS! Cookies saved.")
        print("=" * 60)
        print("\nTo copy cookies to Docker container, run:")
        print(f"  docker cp {TMP_COOKIE_FILE} liga-news-backend:/app/data/x_cookies.json")
        print("\nThen restart the backend or wait for next fetch cycle.")
        print("=" * 60 + "\n")

        input("Press Enter to close browser and exit...")
        await browser.close()

        return True


async def test_saved_cookies():
    """Test if saved cookies still work."""

    if not COOKIE_FILE.exists():
        logger.error(f"No saved cookies found at {COOKIE_FILE}")
        logger.info("Run this script without arguments to login and save cookies.")
        return False

    with open(COOKIE_FILE) as f:
        cookies = json.load(f)

    logger.info(f"Loaded {len(cookies)} cookies from file")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        # Add saved cookies
        await context.add_cookies(cookies)

        page = await context.new_page()

        # Test on a blocked profile
        logger.info("Testing on cdu_fraktion (blocked profile)...")
        await page.goto("https://x.com/cdu_fraktion", wait_until="domcontentloaded")
        await asyncio.sleep(5)

        tweets = await page.query_selector_all('[data-testid="tweet"]')

        await browser.close()

        if tweets:
            logger.info(f"SUCCESS! Cookies are valid. Found {len(tweets)} tweets.")
            return True
        else:
            logger.warning("Cookies appear to be expired or invalid.")
            logger.info("Run this script without --test to re-login.")
            return False


def main():
    import sys

    if "--test" in sys.argv:
        success = asyncio.run(test_saved_cookies())
    else:
        success = asyncio.run(login_and_save_cookies())

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
