#!/usr/bin/env python3
"""
Extract X.com cookies from Chrome browser.

Uses browser-cookie3 library for reliable cookie decryption.
Run this when cookies expire to refresh authentication.

Usage:
    python scripts/extract_chrome_cookies.py
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
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "x_cookies.json"
TMP_OUTPUT = Path("/tmp/x_cookies.json")


def extract_cookies():
    """Extract X.com cookies from Chrome."""
    print("Extracting X.com cookies from Chrome...")

    # Get cookies from Chrome for x.com domain
    try:
        cj = browser_cookie3.chrome(domain_name='.x.com')
    except Exception as e:
        print(f"Error accessing Chrome cookies: {e}")
        print("\nMake sure:")
        print("  1. Chrome is installed")
        print("  2. You're logged into X.com in Chrome")
        print("  3. Chrome is closed (or at least x.com tabs)")
        return False

    cookies = []
    for cookie in cj:
        cookies.append({
            'name': cookie.name,
            'value': cookie.value,
            'domain': cookie.domain,
            'path': cookie.path,
            'secure': True if cookie.secure else False,
            'httpOnly': True if cookie.has_nonstandard_attr('HttpOnly') else False,
            'sameSite': 'Lax',
        })

    print(f"Found {len(cookies)} X.com cookies")

    # Check for essential cookies
    auth_token = next((c for c in cookies if c['name'] == 'auth_token'), None)
    ct0 = next((c for c in cookies if c['name'] == 'ct0'), None)

    print(f"  auth_token: {'✓' if auth_token else '✗'}")
    print(f"  ct0: {'✓' if ct0 else '✗'}")

    if not auth_token:
        print("\n❌ auth_token not found!")
        print("Make sure you're logged into X.com in Chrome.")
        return False

    # Save cookies
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(cookies, f, indent=2)
    print(f"\n✓ Cookies saved to {OUTPUT_FILE}")

    with open(TMP_OUTPUT, 'w') as f:
        json.dump(cookies, f, indent=2)
    print(f"✓ Cookies also saved to {TMP_OUTPUT}")

    print(f"\nTo copy to Docker container:")
    print(f"  docker cp {TMP_OUTPUT} liga-news-backend:/app/data/x_cookies.json")

    return True


def test_cookies():
    """Test if saved cookies are valid by checking their format."""
    if not TMP_OUTPUT.exists():
        print("No cookies file found. Run extract_cookies first.")
        return False

    with open(TMP_OUTPUT) as f:
        cookies = json.load(f)

    auth = next((c for c in cookies if c['name'] == 'auth_token'), None)
    if auth and len(auth['value']) > 20:
        print(f"✓ Cookies look valid (auth_token: {auth['value'][:20]}...)")
        return True
    else:
        print("✗ Cookies appear invalid")
        return False


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        test_cookies()
    else:
        success = extract_cookies()
        sys.exit(0 if success else 1)
