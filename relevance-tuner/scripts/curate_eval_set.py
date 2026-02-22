#!/usr/bin/env python3
"""Build a fixed evaluation set with haiku-verified ground truth.

Selects ~150 items (50 positives, 50 negatives, 50 edge cases) from the
production API, snapshots their content, and verifies labels via Claude haiku.

The eval set is NEVER used for training (data leakage). It's strictly for
measuring prompt/model quality across iterations.

Usage:
    # Dry run — show candidate selection without haiku verification
    python scripts/curate_eval_set.py --dry-run

    # Full curation with haiku verification
    python scripts/curate_eval_set.py

    # Re-verify existing eval set (re-run haiku on all items)
    python scripts/curate_eval_set.py --verify-only

    # Add specific items to existing eval set
    python scripts/curate_eval_set.py --add-items 26105,26786

    # Custom API URL (e.g., via SSH tunnel to prod)
    API_URL=http://localhost:9000/api python scripts/curate_eval_set.py

Requires:
    - API access (local or via SSH tunnel: ssh -L 9000:localhost:8000 docker-ai)
    - `claude` CLI installed (for haiku verification)
    - `requests` package
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# ============================================================================
# Configuration
# ============================================================================

API_URL = os.environ.get("API_URL", "http://localhost:8000/api")
EVAL_SET_PATH = Path(__file__).parent.parent / "evaluations" / "eval_set.json"
HAIKU_MODEL = "haiku"

# Items from compare_llm_models.py TEST_SET (already haiku-verified)
KNOWN_TEST_SET_IDS = [
    26123, 26786, 26729, 26503, 26246, 26848, 26795,  # irrelevant
    26105, 26539, 27028, 27030, 27145,  # relevant
    26600, 26433, 26560, 26753, 26410, 26588,  # edge cases
    26903, 26718,  # additional
]

# AK definitions for haiku prompt
AK_DEFINITIONS = """
- AK1: Grundsatz/Sozialpolitik (Haushalt, Förderungen, Tarifpolitik)
- AK2: Migration/Flucht (Asyl, Beratung, Integration)
- AK3: Gesundheit/Pflege/Senioren (Altenpflege, Krankenhäuser, Hospiz)
- AK4: Eingliederungshilfe (Behinderung, Inklusion, BTHG, WfbM)
- AK5: Kinder/Jugend/Familie (Kita, Jugendhilfe, Frauenhäuser)
- QAG: Querschnitt (Digitalisierung, Wohnen, Schuldnerberatung)
"""


# ============================================================================
# API Helpers
# ============================================================================

def api_get(endpoint: str, params: dict | None = None) -> dict | list | None:
    """GET from the news-aggregator API."""
    try:
        r = requests.get(f"{API_URL}/{endpoint}", params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        print(f"  API {r.status_code}: {endpoint}")
    except Exception as e:
        print(f"  API error: {e}")
    return None


def fetch_item(item_id: int) -> dict | None:
    """Fetch a single item with full content."""
    return api_get(f"items/{item_id}")


def fetch_items(params: dict, max_items: int = 500) -> list[dict]:
    """Fetch items list with filters, paginating as needed (API max page_size=100)."""
    all_items = []
    page = 1
    page_size = min(params.get("page_size", 100), 100)
    params = {k: v for k, v in params.items() if k != "page_size"}

    while len(all_items) < max_items:
        data = api_get("items", {**params, "page_size": page_size, "page": page})
        if not data or "items" not in data or not data["items"]:
            break
        all_items.extend(data["items"])
        if page >= data.get("total_pages", 1):
            break
        page += 1

    return all_items[:max_items]


# ============================================================================
# Candidate Selection
# ============================================================================

def select_positives(count: int = 50) -> list[dict]:
    """Select clear positive (relevant) items with diversity constraints."""
    print(f"\n[1/3] Selecting {count} positive candidates...")

    # Fetch relevant items that have been LLM-processed
    items = fetch_items({"relevant_only": "true"}, max_items=500)

    if not items:
        print("  WARNING: No relevant items found")
        return []

    # Filter: must have LLM processing (summary exists)
    items = [i for i in items if i.get("summary") and not i.get("needs_llm_processing")]
    print(f"  {len(items)} LLM-confirmed relevant items available")

    # Diversity constraints
    source_counts = Counter()
    ak_counts = Counter()
    priority_counts = Counter()
    selected = []

    # Shuffle for diversity (deterministic seed)
    import random
    random.seed(42)
    random.shuffle(items)

    for item in items:
        if len(selected) >= count:
            break

        source = item.get("source", {}).get("name", "Unknown")
        priority = item.get("priority", "medium")
        item_aks = item.get("assigned_aks", [])

        # Max 5 per source
        if source_counts[source] >= 5:
            continue

        # Try to balance priorities (max 25 of any single priority)
        if priority_counts[priority] >= 25:
            continue

        source_counts[source] += 1
        priority_counts[priority] += 1
        for ak in item_aks:
            ak_counts[ak] += 1

        selected.append({
            "id": item["id"],
            "category": "positive",
            "subcategory": f"priority-{priority}",
            "source_name": source,
            "priority": priority,
            "aks": item_aks,
        })

    print(f"  Selected {len(selected)} positives")
    print(f"  Sources: {dict(source_counts)}")
    print(f"  Priorities: {dict(priority_counts)}")
    return selected


def select_negatives(count: int = 50) -> list[dict]:
    """Select clear negative (irrelevant) items with topic diversity."""
    print(f"\n[2/3] Selecting {count} negative candidates...")

    # Fetch irrelevant items (paginate to get enough)
    items = fetch_items({"relevant_only": "false"}, max_items=500)

    if not items:
        print("  WARNING: No irrelevant items found")
        return []

    # Filter: must have been LLM-processed (not pending)
    items = [i for i in items
             if i.get("priority") in (None, "none", "null")
             and not i.get("needs_llm_processing")]

    print(f"  {len(items)} LLM-confirmed irrelevant items available")

    # Diversity: max 3 per source, spread across topic areas
    source_counts = Counter()
    selected = []

    # Shuffle to avoid date clustering
    import random
    random.seed(42)
    random.shuffle(items)

    for item in items:
        if len(selected) >= count:
            break

        source = item.get("source", {}).get("name", "Unknown")
        if source_counts[source] >= 3:
            continue

        # Infer subcategory from title keywords
        title = (item.get("title") or "").lower()
        subcategory = _infer_negative_subcategory(title)

        source_counts[source] += 1
        selected.append({
            "id": item["id"],
            "category": "negative",
            "subcategory": subcategory,
            "source_name": source,
        })

    print(f"  Selected {len(selected)} negatives")
    print(f"  Sources: {dict(source_counts)}")
    subcats = Counter(s["subcategory"] for s in selected)
    print(f"  Subcategories: {dict(subcats)}")
    return selected


def _infer_negative_subcategory(title: str) -> str:
    """Guess topic category from title for diversity tracking."""
    sport_kw = ["fußball", "bundesliga", "champions", "bayern", "dortmund", "sport",
                "tor", "spiel", "liga", "handball", "tennis", "olymp"]
    crime_kw = ["mord", "polizei", "festnahme", "angeklagt", "verurteil", "gericht",
                "verdächt", "straftat", "kriminal"]
    intl_kw = ["usa", "trump", "china", "russland", "ukraine", "israel", "gaza",
               "nato", "eu-", "europa", "international"]
    lifestyle_kw = ["rezept", "kochen", "garten", "mode", "reise", "lifestyle",
                    "fitness", "wetter", "horoskop", "tipp"]
    culture_kw = ["kino", "film", "theater", "musik", "konzert", "ausstellung",
                  "kunst", "buch", "roman", "nachruf"]

    for kw in sport_kw:
        if kw in title:
            return "sports"
    for kw in crime_kw:
        if kw in title:
            return "crime"
    for kw in intl_kw:
        if kw in title:
            return "international"
    for kw in lifestyle_kw:
        if kw in title:
            return "lifestyle"
    for kw in culture_kw:
        if kw in title:
            return "culture"
    return "other"


def select_edge_cases(count: int = 50) -> list[dict]:
    """Select edge cases: disagreements, low confidence, known difficult items."""
    print(f"\n[3/3] Selecting {count} edge case candidates...")

    candidates = []
    seen_ids = set()

    # 1. Include items from compare_llm_models.py TEST_SET
    print("  Adding known test set items...")
    for item_id in KNOWN_TEST_SET_IDS:
        if item_id not in seen_ids:
            candidates.append({
                "id": item_id,
                "category": "edge_case",
                "subcategory": "known-test-set",
            })
            seen_ids.add(item_id)
    print(f"    {len(candidates)} from TEST_SET")

    # 2. Disagreements (classifier vs LLM)
    print("  Fetching disagreements...")
    disagreements = api_get("analytics/disagreements")
    if disagreements and isinstance(disagreements, list):
        for d in disagreements[:20]:
            item_id = d.get("item_id")
            if item_id and item_id not in seen_ids:
                candidates.append({
                    "id": item_id,
                    "category": "edge_case",
                    "subcategory": "disagreement",
                })
                seen_ids.add(item_id)
        print(f"    {len([c for c in candidates if c['subcategory'] == 'disagreement'])} disagreements")

    # 3. Borderline items: recently fetched items that are near the relevance boundary
    #    (items with low priority or items that were relevant but have sparse content)
    print("  Looking for borderline items...")
    recent = fetch_items({"relevant_only": "true"}, max_items=200)
    if recent:
        # Items with low priority are closest to the boundary
        borderline = [i for i in recent
                      if i.get("priority") == "low"
                      and not i.get("needs_llm_processing")
                      and i["id"] not in seen_ids]
        for item in borderline[:15]:
            candidates.append({
                "id": item["id"],
                "category": "edge_case",
                "subcategory": "borderline-relevant",
            })
            seen_ids.add(item["id"])
        print(f"    {len([c for c in candidates if c['subcategory'] == 'borderline-relevant'])} borderline items")

    # 4. Try to load haiku verification files from /tmp
    for path_pattern in ["/tmp/haiku_v4_relevant.txt", "/tmp/haiku_v4_irrelevant.txt",
                         "/tmp/haiku_v3_relevant.txt", "/tmp/haiku_v3_irrelevant.txt"]:
        path = Path(path_pattern)
        if path.exists():
            print(f"  Loading IDs from {path}...")
            try:
                for line in path.read_text().strip().split("\n"):
                    line = line.strip()
                    if line.isdigit():
                        item_id = int(line)
                        if item_id not in seen_ids:
                            candidates.append({
                                "id": item_id,
                                "category": "edge_case",
                                "subcategory": "haiku-flagged",
                            })
                            seen_ids.add(item_id)
            except Exception:
                pass

    # Trim to count
    if len(candidates) > count:
        candidates = candidates[:count]

    print(f"  Selected {len(candidates)} edge cases")
    subcats = Counter(c["subcategory"] for c in candidates)
    print(f"  Subcategories: {dict(subcats)}")
    return candidates


# ============================================================================
# Content Snapshot
# ============================================================================

def snapshot_items(candidates: list[dict]) -> list[dict]:
    """Fetch full content for each candidate and create snapshot records."""
    print(f"\nSnapshotting {len(candidates)} items from API...")
    items = []
    errors = 0

    for i, cand in enumerate(candidates, 1):
        item_id = cand["id"]
        data = fetch_item(item_id)
        if not data:
            print(f"  [{i}/{len(candidates)}] {item_id}: MISSING — skipping")
            errors += 1
            continue

        record = {
            "id": item_id,
            "category": cand["category"],
            "subcategory": cand.get("subcategory", ""),
            "title": data.get("title", ""),
            "content": data.get("content", "") or "",
            "source": data.get("source", {}).get("name", "Unknown"),
            "published_at": data.get("published_at", ""),
            "url": data.get("url", ""),
            "original_labels": {
                "llm_relevant": data.get("priority") not in (None, "none", "null"),
                "llm_priority": data.get("priority"),
                "llm_aks": data.get("assigned_aks", []),
                "needs_llm_processing": data.get("needs_llm_processing", False),
            },
            "ground_truth": None,
            "haiku_verification": None,
            "note": cand.get("note", ""),
        }
        items.append(record)

        if i % 20 == 0:
            print(f"  [{i}/{len(candidates)}] fetched...")

    print(f"  Snapshotted {len(items)} items ({errors} errors)")
    return items


# ============================================================================
# Haiku Verification
# ============================================================================

HAIKU_PROMPT_TEMPLATE = """Du bist ein Experte für deutsche Sozialpolitik. Klassifiziere diesen Nachrichtenartikel für die Liga der Freien Wohlfahrtspflege Hessen.

Die Liga ist eine LOBBY- UND ADVOCACY-ORGANISATION: Dachverband der 6 Wohlfahrtsverbände (AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden) mit 7.300 Einrichtungen und 113.000 Beschäftigten in Hessen.

Arbeitskreise:
{aks}

RELEVANT wenn es um Gesetze, Haushalte, strukturelle Krisen oder politische Entscheidungen im Sozialbereich geht, die Liga für Lobbying nutzen kann.
NICHT RELEVANT: Sport, Entertainment, Lifestyle, Kriminalität ohne Sozialbezug, andere Bundesländer ohne Bundesbezug, Personalien, PR-Events.

Artikel:
Titel: {title}
Quelle: {source}
Inhalt: {content}

Antworte NUR mit validem JSON:
{{"relevant": true/false, "priority": "high"|"medium"|"low"|null, "aks": ["AK1"], "reasoning": "Kurze Begründung"}}"""


def verify_with_haiku(item: dict) -> dict | None:
    """Run haiku verification on a single item via claude CLI."""
    content = item["content"][:3000]  # Limit for haiku context
    if not content:
        content = item["title"]

    prompt = HAIKU_PROMPT_TEMPLATE.format(
        aks=AK_DEFINITIONS.strip(),
        title=item["title"],
        source=item["source"],
        content=content,
    )

    try:
        result = subprocess.run(
            ["claude", "-p", "--model", HAIKU_MODEL, "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            print(f"    haiku error: {result.stderr[:200]}")
            return None

        response = result.stdout.strip()

        # Parse JSON from response
        parsed = _parse_haiku_response(response)
        if parsed:
            return {
                "model": "claude-haiku-4-5-20251001",
                "verified_at": datetime.now().isoformat(),
                "raw_response": response,
                "parsed": parsed,
            }
        else:
            print(f"    haiku parse error: {response[:200]}")
            return None

    except subprocess.TimeoutExpired:
        print(f"    haiku timeout")
        return None
    except FileNotFoundError:
        print("    ERROR: 'claude' CLI not found. Install it first.")
        return None
    except Exception as e:
        print(f"    haiku exception: {e}")
        return None


def _parse_haiku_response(text: str) -> dict | None:
    """Extract JSON from haiku response."""
    # Strip markdown code blocks
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            relevant = data.get("relevant", False)
            priority = data.get("priority")
            if not relevant:
                priority = None
            return {
                "relevant": bool(relevant),
                "priority": priority,
                "aks": data.get("aks", []),
                "reasoning": data.get("reasoning", ""),
            }
        except json.JSONDecodeError:
            pass
    return None


def run_haiku_verification(items: list[dict]) -> list[dict]:
    """Run haiku verification on all items."""
    print(f"\nRunning haiku verification on {len(items)} items...")
    verified = 0
    failed = 0

    for i, item in enumerate(items, 1):
        item_id = item["id"]
        title_short = item["title"][:50]

        # Skip if already verified
        if item.get("haiku_verification") and not item.get("_reverify"):
            print(f"  [{i}/{len(items)}] {item_id}: already verified — skipping")
            verified += 1
            continue

        result = verify_with_haiku(item)
        if result:
            item["haiku_verification"] = result
            item["ground_truth"] = result["parsed"]
            verified += 1
            rel = "REL" if result["parsed"]["relevant"] else "IRR"
            pri = result["parsed"].get("priority") or "none"
            print(f"  [{i}/{len(items)}] {item_id}: {rel}/{pri} — {title_short}")
        else:
            failed += 1
            print(f"  [{i}/{len(items)}] {item_id}: FAILED — {title_short}")

        # Rate limit: small delay between calls
        if i < len(items):
            time.sleep(0.5)

    print(f"\n  Verified: {verified}, Failed: {failed}")
    return items


# ============================================================================
# Eval Set I/O
# ============================================================================

def save_eval_set(items: list[dict], path: Path):
    """Save eval set to JSON."""
    # Compute stats
    cats = Counter(i["category"] for i in items)
    verified = sum(1 for i in items if i.get("ground_truth"))

    eval_set = {
        "version": 1,
        "created_at": datetime.now().isoformat(),
        "api_url": API_URL,
        "items": items,
        "stats": {
            "total": len(items),
            "positives": cats.get("positive", 0),
            "negatives": cats.get("negative", 0),
            "edge_cases": cats.get("edge_case", 0),
            "verified": verified,
        },
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(eval_set, indent=2, ensure_ascii=False))
    print(f"\nEval set saved: {path}")
    print(f"  Total: {len(items)}, Verified: {verified}")
    print(f"  Positives: {cats.get('positive', 0)}, Negatives: {cats.get('negative', 0)}, Edge cases: {cats.get('edge_case', 0)}")


def load_eval_set(path: Path) -> list[dict]:
    """Load existing eval set."""
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data.get("items", [])


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Curate fixed evaluation set with haiku ground truth")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show candidate selection without haiku verification")
    parser.add_argument("--verify-only", action="store_true",
                        help="Re-verify existing eval set items")
    parser.add_argument("--add-items", type=str, default=None,
                        help="Add specific item IDs (comma-separated)")
    parser.add_argument("--positives", type=int, default=50,
                        help="Number of positive items (default: 50)")
    parser.add_argument("--negatives", type=int, default=50,
                        help="Number of negative items (default: 50)")
    parser.add_argument("--edge-cases", type=int, default=50,
                        help="Number of edge cases (default: 50)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output path (default: evaluations/eval_set.json)")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else EVAL_SET_PATH

    print("=" * 70)
    print("EVAL SET CURATION")
    print("=" * 70)
    print(f"  API: {API_URL}")
    print(f"  Output: {output_path}")
    print(f"  Mode: {'dry-run' if args.dry_run else 'verify-only' if args.verify_only else 'full'}")

    # --- Verify-only mode ---
    if args.verify_only:
        items = load_eval_set(output_path)
        if not items:
            print("ERROR: No existing eval set found at", output_path)
            sys.exit(1)
        print(f"\nLoaded {len(items)} existing items")
        for item in items:
            item["_reverify"] = True
        items = run_haiku_verification(items)
        for item in items:
            item.pop("_reverify", None)
        save_eval_set(items, output_path)
        return

    # --- Add-items mode ---
    if args.add_items:
        items = load_eval_set(output_path)
        existing_ids = {i["id"] for i in items}
        new_ids = [int(x.strip()) for x in args.add_items.split(",")]
        new_candidates = []
        for item_id in new_ids:
            if item_id in existing_ids:
                print(f"  {item_id}: already in eval set — skipping")
                continue
            new_candidates.append({
                "id": item_id,
                "category": "edge_case",
                "subcategory": "manual-add",
            })
        if new_candidates:
            new_items = snapshot_items(new_candidates)
            if not args.dry_run:
                new_items = run_haiku_verification(new_items)
            items.extend(new_items)
            save_eval_set(items, output_path)
        else:
            print("No new items to add.")
        return

    # --- Full curation ---

    # Test API connectivity
    test = api_get("admin/stats")
    if test is None:
        print(f"\nERROR: Cannot reach API at {API_URL}")
        print("  Try: ssh -L 9000:localhost:8000 docker-ai -N -f")
        print("  Then: API_URL=http://localhost:9000/api python scripts/curate_eval_set.py")
        sys.exit(1)

    # Select candidates
    positives = select_positives(args.positives)
    negatives = select_negatives(args.negatives)
    edge_cases = select_edge_cases(args.edge_cases)

    all_candidates = positives + negatives + edge_cases

    # Deduplicate by ID
    seen = set()
    deduped = []
    for c in all_candidates:
        if c["id"] not in seen:
            seen.add(c["id"])
            deduped.append(c)
    all_candidates = deduped

    print(f"\n{'=' * 70}")
    print(f"CANDIDATES: {len(all_candidates)} total")
    print(f"  Positives: {len(positives)}")
    print(f"  Negatives: {len(negatives)}")
    print(f"  Edge cases: {len(edge_cases)}")
    print(f"  After dedup: {len(all_candidates)}")

    if args.dry_run:
        print("\n[DRY-RUN] Would snapshot and verify these items.")
        print("\nCandidate IDs:")
        for c in all_candidates:
            print(f"  {c['id']:>6}  {c['category']:<12} {c.get('subcategory', '')}")
        return

    # Snapshot content
    items = snapshot_items(all_candidates)

    # Haiku verification
    items = run_haiku_verification(items)

    # Save
    save_eval_set(items, output_path)


if __name__ == "__main__":
    main()
