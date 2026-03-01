#!/usr/bin/env python3
"""Label classifier-LLM disagreement items with Haiku for cleaner training data.

Fetches items where the classifier said "relevant" but the LLM said "irrelevant",
then labels each with Claude Haiku to get ground truth. Outputs JSONL in the same
format as export_training_data.py, ready to merge as label overrides.

Usage:
    # Fetch and label all disagreements
    python scripts/label_disagreements.py

    # Dry run — show what would be labeled
    python scripts/label_disagreements.py --dry-run

    # Resume from partial run (skips already-labeled items)
    python scripts/label_disagreements.py --resume

    # Custom API URL (e.g., via SSH tunnel to prod)
    API_URL=http://localhost:9000/api python scripts/label_disagreements.py

Requires:
    - API access (local or via SSH tunnel: ssh -L 9000:localhost:8000 docker-ai -N -f)
    - `claude` CLI installed (for haiku labeling)
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

# ============================================================================
# Configuration
# ============================================================================

API_URL = os.environ.get("API_URL", "http://localhost:8000/api")
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "haiku_disagreements.jsonl"
HAIKU_MODEL = "haiku"

# AK definitions (same as curate_eval_set.py)
AK_DEFINITIONS = """
- AK1: Grundsatz/Sozialpolitik (Haushalt, Förderungen, Tarifpolitik)
- AK2: Migration/Flucht (Asyl, Beratung, Integration)
- AK3: Gesundheit/Pflege/Senioren (Altenpflege, Krankenhäuser, Hospiz)
- AK4: Eingliederungshilfe (Behinderung, Inklusion, BTHG, WfbM)
- AK5: Kinder/Jugend/Familie (Kita, Jugendhilfe, Frauenhäuser)
- QAG: Querschnitt (Digitalisierung, Wohnen, Schuldnerberatung)
"""

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


def fetch_disagreement_ids_from_db() -> list[int]:
    """Get disagreement item IDs by querying prod DB via SSH.

    Finds items where classifier_override said relevant=true but the item
    has priority='none' (LLM said irrelevant). These are in item_processing_logs
    but not exposed via the analytics API.
    """
    db_host = os.environ.get("DB_HOST", "docker-ai")
    db_query = (
        "SELECT DISTINCT i.id FROM items i "
        "JOIN item_processing_logs c ON c.item_id = i.id "
        "AND c.step_type = 'classifier_override' "
        "WHERE i.summary IS NOT NULL "
        "AND c.relevant = true AND i.priority = 'none' "
        "ORDER BY i.id"
    )

    try:
        # Use ssh with quoted command to preserve the SQL query
        ssh_cmd = f"docker exec liga-news-db psql -U liga -d liga_news -t -A -c \"{db_query}\""
        result = subprocess.run(
            ["ssh", db_host, ssh_cmd],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"  DB query error: {result.stderr[:200]}")
            return []

        ids = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.isdigit():
                ids.append(int(line))
        return ids

    except subprocess.TimeoutExpired:
        print("  DB query timeout")
        return []
    except Exception as e:
        print(f"  DB query error: {e}")
        return []


def fetch_disagreements() -> list[dict]:
    """Fetch items where classifier said relevant but LLM said irrelevant."""
    print("  Querying prod DB for disagreement IDs...")
    ids = fetch_disagreement_ids_from_db()
    if not ids:
        print("  ERROR: No IDs found. Check SSH access to docker-ai.")
        return []

    print(f"  Found {len(ids)} disagreement IDs")

    # Fetch item details via API
    print(f"  Fetching item details from API...")
    items = []
    for i, item_id in enumerate(ids, 1):
        item = fetch_item(item_id)
        if item:
            items.append(item)
        else:
            print(f"    {item_id}: FETCH FAILED")

        if i % 50 == 0:
            print(f"    Fetched {i}/{len(ids)}...")

    print(f"  Fetched {len(items)} items")
    return items


def fetch_item(item_id: int) -> dict | None:
    """Fetch a single item with full content."""
    return api_get(f"items/{item_id}")


# ============================================================================
# Haiku Labeling
# ============================================================================

def label_with_haiku(title: str, content: str, source: str) -> dict | None:
    """Label a single item with Haiku via claude CLI."""
    content_truncated = content[:3000]
    if not content_truncated:
        content_truncated = title

    prompt = HAIKU_PROMPT_TEMPLATE.format(
        aks=AK_DEFINITIONS.strip(),
        title=title,
        source=source,
        content=content_truncated,
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

        return parse_haiku_response(result.stdout.strip())

    except subprocess.TimeoutExpired:
        print(f"    haiku timeout")
        return None
    except FileNotFoundError:
        print("    ERROR: 'claude' CLI not found")
        return None
    except Exception as e:
        print(f"    haiku exception: {e}")
        return None


def parse_haiku_response(text: str) -> dict | None:
    """Extract JSON from haiku response."""
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


# ============================================================================
# Training Data Format
# ============================================================================

def to_training_format(item: dict, haiku_labels: dict) -> dict:
    """Convert item + haiku labels to training JSONL format."""
    source = item.get("source", {})
    source_name = source.get("name", "unknown") if isinstance(source, dict) else str(source)
    published = item.get("published_at", "")
    date_str = published[:10] if published else ""

    return {
        "input": {
            "title": item.get("title", ""),
            "content": (item.get("content", "") or "")[:5000],
            "source": source_name,
            "date": date_str,
        },
        "labels": {
            "relevant": haiku_labels["relevant"],
            "priority": haiku_labels["priority"],
            "ak": haiku_labels["aks"][0] if haiku_labels["aks"] else None,
            "aks": haiku_labels["aks"],
            "reaction_type": None,
        },
        "provenance": {
            "source_type": "news",
            "item_id": item.get("id"),
            "is_disagreement": True,
            "label_source": "haiku",
            "labeled_at": datetime.now().isoformat(),
            "exported_at": datetime.now().isoformat(),
        },
    }


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Label classifier-LLM disagreements with Haiku"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show disagreements without labeling")
    parser.add_argument("--resume", action="store_true",
                        help="Skip items already in output file")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help=f"Output path (default: {OUTPUT_PATH})")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max items to label (0 = all)")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else OUTPUT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("LABEL DISAGREEMENTS WITH HAIKU")
    print("=" * 70)
    print(f"  API: {API_URL}")
    print(f"  Output: {output_path}")

    # Load already-labeled IDs for resume
    labeled_ids = set()
    if args.resume and output_path.exists():
        with open(output_path) as f:
            for line in f:
                try:
                    record = json.loads(line)
                    item_id = record.get("provenance", {}).get("item_id")
                    if item_id:
                        labeled_ids.add(item_id)
                except json.JSONDecodeError:
                    pass
        print(f"  Resume: {len(labeled_ids)} items already labeled")

    # Fetch disagreements (returns full item dicts)
    print("\nFetching disagreements...")
    items = fetch_disagreements()
    if not items:
        print("ERROR: No disagreements found. Check SSH/API connectivity.")
        sys.exit(1)

    print(f"\n  Total disagreements: {len(items)}")

    # Filter out already-labeled items
    if labeled_ids:
        items = [d for d in items if d.get("id") not in labeled_ids]
        print(f"  After resume filter: {len(items)}")

    if args.limit > 0:
        items = items[:args.limit]
        print(f"  After limit: {len(items)}")

    if args.dry_run:
        print(f"\n[DRY RUN] Would label {len(items)} items with Haiku.")
        print("\nSample items:")
        for item in items[:10]:
            title = item.get("title", "")[:60]
            print(f"  {item.get('id', '?'):>6}  {title}")
        return

    # Label each item
    print(f"\nLabeling {len(items)} items with Haiku...")
    results = {"labeled": 0, "failed": 0, "relevant": 0, "irrelevant": 0}

    # Open output file in append mode (for resume support)
    mode = "a" if args.resume and output_path.exists() else "w"
    with open(output_path, mode) as f:
        for i, item in enumerate(items, 1):
            item_id = item.get("id")
            title_short = (item.get("title") or "")[:50]

            source = item.get("source", {})
            source_name = source.get("name", "Unknown") if isinstance(source, dict) else str(source)

            # Label with Haiku
            haiku_result = label_with_haiku(
                title=item.get("title", ""),
                content=item.get("content", ""),
                source=source_name,
            )

            if haiku_result:
                record = to_training_format(item, haiku_result)
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()

                rel = "REL" if haiku_result["relevant"] else "IRR"
                pri = haiku_result.get("priority") or "none"
                results["labeled"] += 1
                if haiku_result["relevant"]:
                    results["relevant"] += 1
                else:
                    results["irrelevant"] += 1

                print(f"  [{i}/{len(items)}] {item_id}: {rel}/{pri} — {title_short}")
            else:
                results["failed"] += 1
                print(f"  [{i}/{len(items)}] {item_id}: HAIKU FAILED — {title_short}")

            # Rate limit
            if i < len(items):
                time.sleep(0.3)

    # Summary
    print(f"\n{'=' * 70}")
    print("RESULTS")
    print(f"{'=' * 70}")
    print(f"  Labeled:    {results['labeled']}")
    print(f"  Failed:     {results['failed']}")
    print(f"  Relevant:   {results['relevant']} ({results['relevant']/max(results['labeled'],1)*100:.0f}%)")
    print(f"  Irrelevant: {results['irrelevant']} ({results['irrelevant']/max(results['labeled'],1)*100:.0f}%)")
    print(f"\n  Output: {output_path}")
    print(f"\nNext steps:")
    print(f"  1. Export regular training data:")
    print(f"     python scripts/export_training_data.py --min-content-length 200 --haiku-overrides {output_path}")
    print(f"  2. Retrain classifier:")
    print(f"     EMBEDDING_BACKEND=nomic-v2 python train_embedding_classifier.py")


if __name__ == "__main__":
    main()
