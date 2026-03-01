#!/usr/bin/env python3
"""Inject hard negatives (and hard positives) from eval mismatches into training data.

Reads the latest eval results JSON, extracts false positives and false negatives,
fetches their full content from the prod API, and appends them to train.jsonl
with correct labels so the model learns from its mistakes.

Usage:
    python scripts/inject_hard_negatives.py
    python scripts/inject_hard_negatives.py --eval-results evaluations/results/classifier_2026-02-24_nomic-v2-ak-keywords.json
    python scripts/inject_hard_negatives.py --dry-run

Requires:
    - Eval results JSON with mismatches
    - API access (default: http://localhost:8000/api, or set API_URL)
    - Existing train.jsonl to append to
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request

# Configuration
API_URL = os.environ.get("API_URL", "http://localhost:8000/api")
DATA_DIR = Path(__file__).parent.parent / "data" / "final"
RESULTS_DIR = Path(__file__).parent.parent / "evaluations" / "results"
EVAL_SET_PATH = Path(__file__).parent.parent / "evaluations" / "eval_set.json"

# Valid values
PRIORITY_LEVELS = ["low", "medium", "high"]


def find_latest_eval_results() -> Path | None:
    """Find the most recent classifier eval results file."""
    results = sorted(RESULTS_DIR.glob("classifier_*.json"))
    return results[-1] if results else None


def load_eval_set() -> dict[int, dict]:
    """Load eval set and return items keyed by ID."""
    if not EVAL_SET_PATH.exists():
        return {}
    data = json.loads(EVAL_SET_PATH.read_text())
    return {item["id"]: item for item in data.get("items", [])}


def fetch_item_from_api(item_id: int) -> dict | None:
    """Fetch a single item from the API."""
    url = f"{API_URL}/items/{item_id}"
    try:
        req = Request(url)
        with urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except Exception as e:
        print(f"  Warning: Could not fetch item {item_id}: {e}")
        return None


def item_to_training_format(item: dict, correct_relevant: bool, source_label: str = "hard-negative") -> dict:
    """Convert an item (from eval set or API) to training data format."""
    # Get ground truth from eval set if available
    title = item.get("title", "")
    content = item.get("content", "")

    # Source name handling (eval set vs API format)
    source = item.get("source", "")
    if isinstance(source, dict):
        source = source.get("name", "unknown")

    # Priority and AK from ground truth
    gt = item.get("ground_truth", {})
    if correct_relevant and gt:
        priority = gt.get("priority", "medium")
        ak = gt.get("aks", ["QAG"])[0] if gt.get("aks") else "QAG"
        aks = gt.get("aks", [ak])
    elif correct_relevant:
        priority = item.get("priority", "medium")
        ak = item.get("assigned_ak", "QAG")
        aks = item.get("assigned_aks", [ak]) if item.get("assigned_aks") else [ak]
    else:
        priority = None
        ak = None
        aks = []

    return {
        "input": {
            "title": title,
            "content": content[:5000],
            "source": source,
            "date": "",
        },
        "labels": {
            "relevant": correct_relevant,
            "priority": priority,
            "ak": ak,
            "aks": aks,
            "reaction_type": None,
        },
        "provenance": {
            "source_type": "hard_negative",
            "item_id": item.get("id"),
            "label_source": source_label,
            "is_disagreement": True,
            "exported_at": datetime.now().isoformat(),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Inject hard negatives from eval mismatches")
    parser.add_argument("--eval-results", type=str, default=None,
                        help="Path to eval results JSON (default: latest)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be injected without modifying files")
    parser.add_argument("--api-url", type=str, default=None,
                        help="API URL for fetching item content")
    args = parser.parse_args()

    if args.api_url:
        global API_URL
        API_URL = args.api_url

    # Find eval results
    if args.eval_results:
        results_path = Path(args.eval_results)
    else:
        results_path = find_latest_eval_results()

    if not results_path or not results_path.exists():
        print("ERROR: No eval results found")
        sys.exit(1)

    print("=" * 60)
    print("Inject Hard Negatives from Eval Mismatches")
    print("=" * 60)
    print(f"  Eval results: {results_path.name}")

    # Load eval results
    eval_results = json.loads(results_path.read_text())
    results = eval_results.get("results", [])

    # Load eval set for full content
    eval_set = load_eval_set()

    # Find mismatches
    false_positives = [r for r in results if not r["expected_relevant"] and r["got_relevant"]]
    false_negatives = [r for r in results if r["expected_relevant"] and not r["got_relevant"]]

    print(f"  False positives: {len(false_positives)} (classifier wrongly marked relevant)")
    print(f"  False negatives: {len(false_negatives)} (classifier missed relevant items)")

    # Load existing training data to check for duplicates
    train_path = DATA_DIR / "train.jsonl"
    existing_ids = set()
    if train_path.exists():
        with open(train_path) as f:
            for line in f:
                try:
                    item = json.loads(line)
                    item_id = item.get("provenance", {}).get("item_id")
                    if item_id:
                        existing_ids.add(item_id)
                except json.JSONDecodeError:
                    pass

    print(f"  Existing training items: {len(existing_ids)}")

    # Build injection items
    to_inject = []

    for r in false_positives:
        item_id = r["id"]
        if item_id in existing_ids:
            continue

        # Try eval set first (has full content), then API
        if item_id in eval_set:
            item = eval_set[item_id]
            training_item = item_to_training_format(item, correct_relevant=False, source_label="eval-fp")
            to_inject.append(training_item)
        else:
            api_item = fetch_item_from_api(item_id)
            if api_item:
                training_item = item_to_training_format(api_item, correct_relevant=False, source_label="eval-fp")
                to_inject.append(training_item)

    for r in false_negatives:
        item_id = r["id"]
        if item_id in existing_ids:
            continue

        if item_id in eval_set:
            item = eval_set[item_id]
            training_item = item_to_training_format(item, correct_relevant=True, source_label="eval-fn")
            to_inject.append(training_item)
        else:
            api_item = fetch_item_from_api(item_id)
            if api_item:
                training_item = item_to_training_format(api_item, correct_relevant=True, source_label="eval-fn")
                to_inject.append(training_item)

    # Stats
    new_fps = sum(1 for t in to_inject if not t["labels"]["relevant"])
    new_fns = sum(1 for t in to_inject if t["labels"]["relevant"])
    skipped = (len(false_positives) + len(false_negatives)) - len(to_inject)

    print(f"\n  To inject: {len(to_inject)} items ({new_fps} FPs as irrelevant, {new_fns} FNs as relevant)")
    if skipped > 0:
        print(f"  Skipped: {skipped} (already in training data)")

    if args.dry_run:
        print("\n[DRY RUN] No files modified")
        for item in to_inject:
            label = "IRR" if not item["labels"]["relevant"] else "REL"
            title = item["input"]["title"][:60]
            print(f"  [{label}] {item['provenance']['item_id']}: {title}")
        return

    if not to_inject:
        print("\nNothing to inject!")
        return

    # Append to train.jsonl
    with open(train_path, "a") as f:
        for item in to_inject:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"\nAppended {len(to_inject)} items to {train_path}")

    # Update stats
    stats_path = DATA_DIR / "stats.json"
    if stats_path.exists():
        stats = json.loads(stats_path.read_text())
        stats["hard_negatives_injected"] = len(to_inject)
        stats["hard_negatives_fps"] = new_fps
        stats["hard_negatives_fns"] = new_fns
        stats["hard_negatives_source"] = results_path.name
        stats["train_size"] = stats.get("train_size", 0) + len(to_inject)
        with open(stats_path, "w") as f:
            json.dump(stats, f, indent=2)

    new_total = len(existing_ids) + len(to_inject)
    print(f"  New training set size: {new_total}")


if __name__ == "__main__":
    main()
