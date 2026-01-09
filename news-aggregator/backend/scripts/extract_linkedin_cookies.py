#!/usr/bin/env python3
"""
Extract LinkedIn cookies from Chrome browser.

Uses browser-cookie3 library for reliable cookie decryption.
Run this when cookies expire to refresh authentication.

Usage:
    python scripts/extract_linkedin_cookies.py

Prerequisites:
    1. Chrome must be installed
    2. You must be logged into LinkedIn in Chrome
    3. Close Chrome (or at least LinkedIn tabs) before running
"""

import json
from pathlib import Path

try:
    import browser_cookie3
except ImportError:
    print("Installing browser-cookie3...")
    import subprocess

    subprocess.run(["pip", "install", "browser-cookie3", "-q"])
    import browser_cookie3

# Output paths
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "linkedin_cookies.json"
TMP_OUTPUT = Path("/tmp/linkedin_cookies.json")


def extract_cookies():
    """Extract LinkedIn cookies from Chrome."""
    print("Extracting LinkedIn cookies from Chrome...")

    # Get cookies from Chrome for linkedin.com domain
    try:
        cj = browser_cookie3.chrome(domain_name=".linkedin.com")
    except Exception as e:
        print(f"Error accessing Chrome cookies: {e}")
        print("\nMake sure:")
        print("  1. Chrome is installed")
        print("  2. You're logged into LinkedIn in Chrome")
        print("  3. Chrome is closed (or at least LinkedIn tabs)")
        return False

    cookies = []
    for cookie in cj:
        cookies.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": True if cookie.secure else False,
                "httpOnly": True if cookie.has_nonstandard_attr("HttpOnly") else False,
                "sameSite": "Lax",
            }
        )

    print(f"Found {len(cookies)} LinkedIn cookies")

    # Check for essential cookies
    li_at = next((c for c in cookies if c["name"] == "li_at"), None)
    jsessionid = next((c for c in cookies if c["name"] == "JSESSIONID"), None)
    lidc = next((c for c in cookies if c["name"] == "lidc"), None)

    print(f"  li_at (session): {'found' if li_at else 'MISSING'}")
    print(f"  JSESSIONID: {'found' if jsessionid else 'MISSING'}")
    print(f"  lidc: {'found' if lidc else 'MISSING'}")

    if not li_at:
        print("\nli_at cookie not found!")
        print("Make sure you're logged into LinkedIn in Chrome.")
        return False

    # Save cookies
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(cookies, f, indent=2)
    print(f"\nCookies saved to {OUTPUT_FILE}")

    with open(TMP_OUTPUT, "w") as f:
        json.dump(cookies, f, indent=2)
    print(f"Cookies also saved to {TMP_OUTPUT}")

    print(f"\nTo copy to Docker container:")
    print(f"  docker cp {TMP_OUTPUT} liga-news-backend:/app/data/linkedin_cookies.json")

    return True


def test_cookies():
    """Test if saved cookies are valid by checking their format."""
    if not TMP_OUTPUT.exists():
        print("No cookies file found. Run extract_cookies first.")
        return False

    with open(TMP_OUTPUT) as f:
        cookies = json.load(f)

    li_at = next((c for c in cookies if c["name"] == "li_at"), None)
    if li_at and len(li_at["value"]) > 20:
        print(f"Cookies look valid (li_at: {li_at['value'][:20]}...)")
        return True
    else:
        print("Cookies appear invalid")
        return False


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        test_cookies()
    else:
        success = extract_cookies()
        sys.exit(0 if success else 1)
