# Proxy Pool

Maintains two pools of free public proxies for connectors that need to avoid
rate limits or geo-blocks. `backend/services/proxy_manager.py`.

| Pool | Used for | Target | Behaviour when short |
|------|----------|--------|----------------------|
| HTTP | Plain-HTTP fetches | `PROXY_POOL_MIN` (20) | Refill every cycle until met |
| HTTPS | CONNECT tunnels (X/Twitter) | `PROXY_HTTPS_POOL_TARGET` (20) | Best effort; falls back to a direct connection |

## Why HTTPS is a target, not a minimum

A CONNECT-capable proxy is far rarer than a plain-HTTP one, and free lists churn
hourly. Treating 20 as a *minimum* would mean every fill cycle ends "unfilled"
and re-runs at full effort forever. So the two numbers are separate:

- **target** (`PROXY_HTTPS_POOL_TARGET`, 20) — keep collecting up to this many.
  A cap, not a promise.
- **floor** (`PROXY_HTTPS_POOL_FLOOR`, 2) — below this the pool is genuinely
  degraded and we log a WARNING. Between floor and target we log INFO: 5/20 is
  a normal day, not an incident.
- **probe budget** (`PROXY_HTTPS_PROBE_BUDGET`, 100) — at most this many
  CONNECT+TLS probes per fill cycle. This, not the target, is what ends the
  HTTPS search, so a cycle costs a bounded amount on a 2-core VM.

The empty-batch rule that stops the HTTP search deliberately does **not** apply
while HTTPS budget remains: at the observed hit rate, three barren batches in a
row is the normal case, and letting it stop the sweep would throw the budget
away on every cycle.

## Validation

A proxy is only accepted if it demonstrably carries traffic:

- **HTTP**: fetch an IP-echo endpoint through it and check the echoed address is
  neither missing nor our own (a transparent proxy is useless for scraping).
  Redirects are rejected — that means a captive portal.
- **HTTPS**: `CONNECT` must return exactly `200`, *and* a real TLS handshake to
  `x.com` must complete through the tunnel. Some proxies accept CONNECT and then
  serve their own content.

The two are **independent**. A proxy that tunnels fine can refuse plain HTTP, so
CONNECT capability is probed across the whole batch, not just HTTP survivors —
measured 2026-09-02, 4 of every 10 usable HTTPS proxies fail the HTTP probe.
The same applies everywhere an HTTPS proxy is re-checked — the known-good list
on restart, and the 10-minute health check — because retention beats discovery
when hits are this rare. Health-checking the HTTPS pool with the HTTP probe used
to evict tunnel-only proxies within about half an hour.

## Selection order

Both pools are kept sorted **fastest first**, and that order is what selection
reads — so every write to a pool goes through `_sort_pool()`.

- `checkout_proxy()` (X scraper) takes the fastest proxy not already reserved.
  Reservations make concurrent callers walk *down* the list rather than contend
  for one entry, so the ordering spreads load instead of concentrating it.
- `get_next_proxy()` (Instagram, LinkedIn) holds no reservation, so handing
  every caller the single fastest proxy would pile all traffic onto it and get
  it banned — the point of a pool is to spread load. It rotates over the sorted
  pool instead: each pass starts at the fastest and works down. The rotation
  restarts whenever the pool is re-sorted, so a stale index cannot leave it
  stuck in the slow tail.

The HTTPS pool's latency is always the **CONNECT + TLS handshake time**, never a
plain-HTTP latency, because that is the protocol those proxies are used for.
Mixing the two measurements would make the sort compare unlike numbers.

Note that tunnel times are not capped: `MAX_LATENCY_MS` (2500) applies only to
the HTTP probe. Tunnels of 5-12s do get accepted — they sort last and are used
only once the faster ones are busy, which still beats falling back to a direct
connection.

## Choosing sources

**A list being named `https.txt` predicts nothing.** Measured 2026-09-02 by
sampling 40 entries per list and running both checks above:

| Source | Size | HTTPS/40 | HTTP/40 |
|--------|------|----------|---------|
| monosans/proxy-list | 687 | 14 | 4 |
| officialputuid/KangProxy | 1523 | 11 | 7 |
| elliottophellia/proxylist (`http_checked`) | 1045 | 10 | 12 |
| proxyscrape v2 (`ssl=yes`) | 138 | 9 | 9 |
| proxyscrape v4 | 551 | 5 | 7 |
| Zaeem20/FREE_PROXIES_LIST `https.txt` | 409 | 4 | 1 |
| vakhov fresh-proxy-list `http.txt` | 524 | 0 | 16 |
| **MuRongPIG/Proxy-Master** | **20000** | **0** | **3** |

The three best CONNECT sources are general `http` lists that happen to be
freshly checked. The dedicated `https` lists were mostly dead:
`zloi-user/hideip.me` (1080 entries, 0), `roosterkid/HTTPS_RAW` (64, 0),
`vakhov/https` (6, 0).

**Size matters as much as rate.** Proxy-Master alone was 73% of the candidate
pool at a 0% HTTPS rate, so nearly every random batch was drawn from a list that
yields nothing — that, not a code bug, is why the HTTPS pool sat at one proxy.
Dropping it and the other zero-yield lists cut the pool from 27,400 to ~10,100
candidates and raised the expected hit rate from ~2% to ~12% (HTTPS) and ~10% to
~17% (HTTP).

Re-run the measurement before adding or trusting a source; these lists decay.

## Operations

```bash
# Pool status (counts, target, floor, per-proxy latency)
curl -s http://localhost:8000/api/proxies/status | jq .

# Force a refill now
curl -s -X POST http://localhost:8000/api/proxies/refresh
```

Pools start empty and fill in the background. **An initial fill right after a
container restart can legitimately find nothing**: validation uses a 3s timeout
and competes with the rest of app startup on a 2-core VM. The maintenance cycle
(every 10 min) recovers it — check a later cycle before concluding anything is
broken.

Sources are ranked in `PROXY_SOURCES`; the per-source measurement lives in a
comment there so it stays next to the list it justifies.
