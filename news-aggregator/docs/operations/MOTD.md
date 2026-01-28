# Message of the Day (MOTD)

The MOTD feature displays a notification to users when they open the application. The message is shown once per day (resets at midnight Europe/Berlin).

## Target Audience

**Important**: The target audience is a non-technical journalist. She is interested in practical value and improvements, not technical details.

When writing MOTD content:
- Focus on **what changed for the user**, not how it was implemented
- Use simple, non-technical language
- Highlight **benefits and improvements** they'll notice
- Avoid technical jargon (no "API", "database", "proxy", etc.)

### Example: Good vs Bad

**Bad (too technical):**
> We implemented HTTPS tunnel validation for the proxy manager and split the pool into separate HTTP/HTTPS pools with independent thresholds.

**Good (user-focused):**
> Die Abfrage von X/Twitter-Beiträgen ist jetzt zuverlässiger. Sie sollten weniger Fehlermeldungen bei X-Quellen sehen.

**Bad (too technical):**
> Added intra-batch deduplication using Jaccard similarity on title word overlap with 70% threshold to handle race conditions.

**Good (user-focused):**
> Doppelte Artikel werden jetzt besser erkannt. Auch wenn mehrere Quellen gleichzeitig über dasselbe Thema berichten, werden diese nun korrekt gruppiert.

## Usage

### Setting MOTD via API

```bash
# Set a new MOTD
curl -X POST http://localhost:8000/api/motd/admin \
  -H "Content-Type: application/json" \
  -d '{"message": "Neuerungen: Die Duplikaterkennung wurde verbessert.", "active": true}'

# Clear/disable MOTD
curl -X DELETE http://localhost:8000/api/motd/admin

# View MOTD history
curl http://localhost:8000/api/motd/history
```

### Session Behavior

- MOTD is shown **only when it changes** (new ID or updated_at)
- If user dismisses, they won't see the same MOTD again
- If MOTD is updated (even same day), users see the new version
- No daily reset - MOTD only appears when there's actually something new

## Writing Guidelines

When summarizing technical changes for the MOTD, translate them:

| Technical Change | User-Friendly Message |
|-----------------|----------------------|
| Fixed deduplication race condition | Doppelte Artikel werden jetzt zuverlässiger erkannt |
| Added HTTPS proxy support | X/Twitter-Abfragen funktionieren jetzt stabiler |
| Increased PID limits | Weniger Fehlermeldungen bei Browser-Quellen |
| Improved classifier thresholds | Die automatische Priorisierung ist jetzt genauer |

## Database Model

The MOTD is stored in the `motd` table:

```sql
CREATE TABLE motd (
    id SERIAL PRIMARY KEY,
    message TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP,
    created_by VARCHAR(100)
);
```

## Frontend Implementation

The `MOTDModal.vue` component:
1. Fetches MOTD on app load
2. Checks localStorage for previous dismissal
3. Compares dates to determine if session expired
4. Shows modal if MOTD is active and not dismissed today
