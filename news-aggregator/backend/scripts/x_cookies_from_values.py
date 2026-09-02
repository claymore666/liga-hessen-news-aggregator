#!/usr/bin/env python3
"""Build data/x_cookies.json (Playwright context.cookies() format) from a
simple name=value input file, one cookie per line. Values never printed."""
import json
import sys
import time
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
HTTP_ONLY = {"auth_token", "kdt", "twid"}
ONE_YEAR = int(time.time()) + 365 * 86400

cookies = []
for line in src.read_text().splitlines():
    line = line.strip()
    if not line or "=" not in line:
        continue
    name, value = line.split("=", 1)
    name, value = name.strip(), value.strip().strip('"')
    cookies.append({
        "name": name,
        "value": value,
        "domain": ".x.com",
        "path": "/",
        "expires": ONE_YEAR,
        "httpOnly": name in HTTP_ONLY,
        "secure": True,
        "sameSite": "None" if name == "auth_token" else "Lax",
    })

names = {c["name"] for c in cookies}
missing = {"auth_token", "ct0"} - names
if missing:
    sys.exit(f"missing required cookies: {sorted(missing)}")

dst.write_text(json.dumps(cookies, indent=2))
dst.chmod(0o600)
print(f"wrote {dst} with {len(cookies)} cookies: {sorted(names)}")
