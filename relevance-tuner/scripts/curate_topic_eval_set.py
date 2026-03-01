#!/usr/bin/env python3
"""Build a topic assignment evaluation set with Haiku-verified ground truth.

Exports ~200 recent relevant items from production, then uses Claude Haiku
to assign "ground truth" topics from the taxonomy. The result is saved as
evaluations/topic_eval_set.json and used by run_topic_eval.py.

Usage:
    # Full curation (export from prod + haiku verification)
    python scripts/curate_topic_eval_set.py

    # Dry run — show what would be exported
    python scripts/curate_topic_eval_set.py --dry-run

    # Re-verify existing eval set with haiku
    python scripts/curate_topic_eval_set.py --verify-only

    # Custom API URL (e.g., via SSH tunnel to prod)
    API_URL=http://localhost:9000/api python scripts/curate_topic_eval_set.py

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
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# Add parent to path for topic_taxonomy import
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "news-aggregator" / "backend" / "services"))

from topic_taxonomy import TOPIC_TAXONOMY, SONSTIGES

# ============================================================================
# Configuration
# ============================================================================

API_URL = os.environ.get("API_URL", "http://localhost:8000/api")
EVAL_SET_PATH = Path(__file__).parent.parent / "evaluations" / "topic_eval_set.json"
HAIKU_MODEL = "haiku"


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
    """Fetch items list with filters, paginating as needed."""
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

def select_candidates(count: int = 200) -> list[dict]:
    """Select diverse relevant items for topic evaluation."""
    print(f"\nSelecting {count} relevant items for topic evaluation...")

    items = fetch_items({"relevant_only": "true"}, max_items=800)

    if not items:
        print("  WARNING: No relevant items found")
        return []

    # Filter: must have LLM processing and a topic assigned
    items = [
        i for i in items
        if i.get("summary")
        and not i.get("needs_llm_processing")
        and not i.get("similar_to_id")  # skip duplicates
        and i.get("metadata", {}).get("llm_analysis", {}).get("topic")
    ]
    print(f"  {len(items)} LLM-processed relevant items with topics available")

    # Diversity constraints
    source_counts = Counter()
    topic_counts = Counter()
    priority_counts = Counter()
    selected = []

    import random
    random.seed(42)
    random.shuffle(items)

    for item in items:
        if len(selected) >= count:
            break

        source = item.get("source", {}).get("name", "Unknown")
        priority = item.get("priority", "medium")
        current_topic = item.get("metadata", {}).get("llm_analysis", {}).get("topic", "")

        # Max 10 per source for diversity
        if source_counts[source] >= 10:
            continue

        # Max 15 per topic to avoid over-representing common topics
        if topic_counts[current_topic] >= 15:
            continue

        source_counts[source] += 1
        topic_counts[current_topic] += 1
        priority_counts[priority] += 1

        selected.append({
            "id": item["id"],
            "source_name": source,
            "priority": priority,
            "current_topic": current_topic,
        })

    print(f"  Selected {len(selected)} items")
    print(f"  Sources: {len(source_counts)} unique ({', '.join(f'{k}={v}' for k, v in source_counts.most_common(5))}...)")
    print(f"  Priorities: {dict(priority_counts)}")
    print(f"  Topics: {len(topic_counts)} unique ({', '.join(f'{k}={v}' for k, v in topic_counts.most_common(5))}...)")
    return selected


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

        content = data.get("content", "") or ""
        record = {
            "id": item_id,
            "title": data.get("title", ""),
            "content": content[:2000],  # limit for eval
            "source": data.get("source", {}).get("name", "Unknown"),
            "published_at": data.get("published_at", ""),
            "url": data.get("url", ""),
            "priority": cand["priority"],
            "current_topic": cand["current_topic"],
            "ground_truth": None,
        }
        items.append(record)

        if i % 50 == 0:
            print(f"  [{i}/{len(candidates)}] fetched...")

    print(f"  Snapshotted {len(items)} items ({errors} errors)")
    return items


# ============================================================================
# Haiku Verification
# ============================================================================

HAIKU_TOPIC_PROMPT = """Du bist ein Experte für deutsche Sozialpolitik. Ordne diesen Nachrichtenartikel GENAU EINEM Thema aus der folgenden Themenliste zu.

KONTEXT: Die Liga der Freien Wohlfahrtspflege Hessen ist eine Lobby- und Advocacy-Organisation (AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden).

THEMENLISTE:
{taxonomy_list}
- Sonstiges

REGELN:
- Wähle das Thema, das am besten beschreibt, WARUM der Artikel für die Wohlfahrtspflege relevant ist — nicht worum es allgemein geht.
- KEINE Parteinamen, Organisationsnamen oder Ortsnamen als Thema.
- Nur Sonstiges wählen, wenn wirklich KEIN Thema passt. Dann zusätzlich einen Vorschlag angeben.

Artikel:
Titel: {title}
Quelle: {source}
Inhalt: {content}

Antworte NUR mit validem JSON:
{{"topic": "Thema aus Liste", "reasoning": "Kurze Begründung in 1-2 Sätzen"}}
oder bei Sonstiges:
{{"topic": "Sonstiges", "topic_suggestion": "Dein Vorschlag", "reasoning": "Kurze Begründung"}}"""


def verify_topic_with_haiku(item: dict) -> dict | None:
    """Run haiku topic verification on a single item via claude CLI."""
    content = item["content"][:3000]
    if not content:
        content = item["title"]

    taxonomy_list = "\n".join(f"- {t}" for t in TOPIC_TAXONOMY)

    prompt = HAIKU_TOPIC_PROMPT.format(
        taxonomy_list=taxonomy_list,
        title=item["title"],
        source=item["source"],
        content=content,
    )

    try:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            ["claude", "-p", "--model", HAIKU_MODEL, "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )

        if result.returncode != 0:
            print(f"    haiku error: {result.stderr[:200]}")
            return None

        response = result.stdout.strip()
        parsed = _parse_haiku_topic_response(response)
        if parsed:
            return {
                "topic": parsed["topic"],
                "reasoning": parsed.get("reasoning", ""),
                "topic_suggestion": parsed.get("topic_suggestion"),
                "verified_by": "claude-haiku-4-5-20251001",
                "verified_at": datetime.now().isoformat(),
                "raw_response": response,
            }
        else:
            print(f"    haiku parse error: {response[:200]}")
            return None

    except subprocess.TimeoutExpired:
        print("    haiku timeout")
        return None
    except FileNotFoundError:
        print("    ERROR: 'claude' CLI not found. Install it first.")
        return None
    except Exception as e:
        print(f"    haiku exception: {e}")
        return None


def _parse_haiku_topic_response(text: str) -> dict | None:
    """Extract topic JSON from haiku response."""
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            raw_topic = data.get("topic", "")

            # Validate against taxonomy
            topic_lower = raw_topic.strip().lower()
            canonical = SONSTIGES
            for t in TOPIC_TAXONOMY:
                if t.lower() == topic_lower:
                    canonical = t
                    break
            if topic_lower == "sonstiges":
                canonical = SONSTIGES

            return {
                "topic": canonical,
                "reasoning": data.get("reasoning", ""),
                "topic_suggestion": data.get("topic_suggestion"),
            }
        except json.JSONDecodeError:
            pass
    return None


def run_haiku_verification(items: list[dict]) -> list[dict]:
    """Run haiku topic verification on all items."""
    print(f"\nRunning haiku topic verification on {len(items)} items...")
    verified = 0
    failed = 0

    for i, item in enumerate(items, 1):
        item_id = item["id"]
        title_short = item["title"][:50]

        # Skip if already verified
        if item.get("ground_truth") and not item.get("_reverify"):
            print(f"  [{i}/{len(items)}] {item_id}: already verified — skipping")
            verified += 1
            continue

        result = verify_topic_with_haiku(item)
        if result:
            item["ground_truth"] = result
            verified += 1
            topic = result["topic"]
            match = "=" if topic == item["current_topic"] else "≠"
            print(f"  [{i}/{len(items)}] {item_id}: {topic} {match} {item['current_topic']} — {title_short}")
        else:
            failed += 1
            print(f"  [{i}/{len(items)}] {item_id}: FAILED — {title_short}")

        # Rate limit
        if i < len(items):
            time.sleep(0.5)

    print(f"\n  Verified: {verified}, Failed: {failed}")
    return items


# ============================================================================
# Eval Set I/O
# ============================================================================

def save_eval_set(items: list[dict], path: Path):
    """Save topic eval set to JSON."""
    verified = sum(1 for i in items if i.get("ground_truth"))
    topics = Counter(
        i["ground_truth"]["topic"] for i in items if i.get("ground_truth")
    )
    matches = sum(
        1 for i in items
        if i.get("ground_truth") and i["ground_truth"]["topic"] == i["current_topic"]
    )

    eval_set = {
        "version": 1,
        "type": "topic",
        "created_at": datetime.now().isoformat(),
        "description": "Topic assignment evaluation set with Haiku ground truth",
        "api_url": API_URL,
        "taxonomy_size": len(TOPIC_TAXONOMY),
        "items": items,
        "stats": {
            "total": len(items),
            "verified": verified,
            "current_vs_haiku_match": matches,
            "match_rate": round(matches / verified, 4) if verified else 0,
            "unique_topics": len(topics),
            "sonstiges_count": topics.get(SONSTIGES, 0),
            "topic_distribution": dict(topics.most_common()),
        },
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(eval_set, indent=2, ensure_ascii=False))
    print(f"\nTopic eval set saved: {path}")
    print(f"  Total: {len(items)}, Verified: {verified}")
    print(f"  Current vs Haiku match: {matches}/{verified} ({matches/verified:.0%})" if verified else "")
    print(f"  Unique topics: {len(topics)}, Sonstiges: {topics.get(SONSTIGES, 0)}")
    print(f"\n  Top topics:")
    for topic, count in topics.most_common(10):
        print(f"    {topic:35s}: {count}")


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
    parser = argparse.ArgumentParser(
        description="Build topic evaluation set with Haiku ground truth"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show candidate selection without haiku verification")
    parser.add_argument("--verify-only", action="store_true",
                        help="Re-verify existing eval set items")
    parser.add_argument("--count", type=int, default=200,
                        help="Number of items to select (default: 200)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help=f"Output path (default: {EVAL_SET_PATH})")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else EVAL_SET_PATH

    print("=" * 70)
    print("TOPIC EVAL SET CURATION")
    print("=" * 70)
    print(f"  API: {API_URL}")
    print(f"  Output: {output_path}")
    print(f"  Mode: {'dry-run' if args.dry_run else 'verify-only' if args.verify_only else 'full'}")
    print(f"  Taxonomy: {len(TOPIC_TAXONOMY)} topics + Sonstiges")

    # --- Verify-only mode ---
    if args.verify_only:
        items = load_eval_set(output_path)
        if not items:
            print(f"ERROR: No existing eval set found at {output_path}")
            sys.exit(1)
        print(f"\nLoaded {len(items)} existing items")
        for item in items:
            item["_reverify"] = True
        items = run_haiku_verification(items)
        for item in items:
            item.pop("_reverify", None)
        save_eval_set(items, output_path)
        return

    # --- Full curation ---

    # Test API connectivity
    test = api_get("admin/stats")
    if test is None:
        print(f"\nERROR: Cannot reach API at {API_URL}")
        print("  Try: ssh -L 9000:localhost:8000 docker-ai -N -f")
        print("  Then: API_URL=http://localhost:9000/api python scripts/curate_topic_eval_set.py")
        sys.exit(1)

    # Select candidates
    candidates = select_candidates(args.count)

    if not candidates:
        print("ERROR: No candidates selected")
        sys.exit(1)

    if args.dry_run:
        print(f"\n[DRY-RUN] Would snapshot and verify {len(candidates)} items.")
        print("\nTopic distribution in current assignments:")
        topics = Counter(c["current_topic"] for c in candidates)
        for topic, count in topics.most_common():
            print(f"  {topic:35s}: {count}")
        return

    # Snapshot content from API
    items = snapshot_items(candidates)

    # Haiku verification
    items = run_haiku_verification(items)

    # Save
    save_eval_set(items, output_path)


if __name__ == "__main__":
    main()
