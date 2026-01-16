#!/usr/bin/env python3
"""Export training data from news-aggregator database for embedding classifier.

This script exports LLM-curated items directly from the production database.
No re-labeling needed - items already have relevance, priority, and AK labels.

Usage:
    python scripts/export_training_data.py              # Export all items
    python scripts/export_training_data.py --dry-run   # Show stats only
    python scripts/export_training_data.py --output data/final  # Custom output dir
"""

import argparse
import json
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request

# Configuration
API_URL = "http://localhost:8000/api"
PAGE_SIZE = 100
RANDOM_SEED = 42

# Valid values (must match config.py)
PRIORITY_LEVELS = ["low", "medium", "high"]
AK_CLASSES = ["AK1", "AK2", "AK3", "AK4", "AK5", "QAG",
              "QAG_DIGITALISIERUNG", "QAG_WOHNEN", "QAG_KLIMASCHUTZ"]


def fetch_all_items(relevant_only: bool = False) -> list[dict]:
    """Fetch all items from API."""
    items = []
    page = 1

    while True:
        url = f"{API_URL}/items?page={page}&page_size={PAGE_SIZE}&relevant_only={'true' if relevant_only else 'false'}"

        try:
            req = Request(url)
            with urlopen(req, timeout=30) as resp:
                data = json.load(resp)
                batch = data.get("items", [])
                total = data.get("total", 0)

                if not batch:
                    break

                items.extend(batch)
                print(f"  Page {page}: {len(batch)} items (total: {len(items)}/{total})")

                if len(items) >= total:
                    break
                page += 1
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break

    return items


def convert_to_training_format(item: dict) -> dict:
    """Convert API item to training data format."""
    # Determine relevance from priority
    priority = item.get("priority", "none")
    is_relevant = priority in PRIORITY_LEVELS

    # Get source name
    source = item.get("source", {})
    source_name = source.get("name", "unknown") if isinstance(source, dict) else str(source)

    # Get published date
    published = item.get("published_at", "")
    date_str = published[:10] if published else ""

    # Get AK assignments (single and multi)
    primary_ak = item.get("assigned_ak") if is_relevant else None
    all_aks = item.get("assigned_aks", []) if is_relevant else []

    # Ensure all_aks is a list and includes primary_ak
    if not all_aks and primary_ak:
        all_aks = [primary_ak]

    return {
        "input": {
            "title": item.get("title", ""),
            "content": item.get("content", "")[:5000],  # Truncate very long content
            "source": source_name,
            "date": date_str
        },
        "labels": {
            "relevant": is_relevant,
            "priority": priority if is_relevant else None,
            "ak": primary_ak,  # Primary AK for single-label compatibility
            "aks": all_aks,    # All AKs for multi-label training
            "reaction_type": None
        },
        "provenance": {
            "source_type": "news",
            "item_id": item.get("id"),
            "exported_at": datetime.now().isoformat()
        }
    }


def create_splits(items: list[dict], train_ratio: float = 0.7, val_ratio: float = 0.15):
    """Create stratified train/val/test splits."""
    random.seed(RANDOM_SEED)

    # Separate relevant and irrelevant
    relevant = [i for i in items if i["labels"]["relevant"]]
    irrelevant = [i for i in items if not i["labels"]["relevant"]]

    random.shuffle(relevant)
    random.shuffle(irrelevant)

    def split_list(lst, train_r, val_r):
        n = len(lst)
        train_end = int(n * train_r)
        val_end = int(n * (train_r + val_r))
        return lst[:train_end], lst[train_end:val_end], lst[val_end:]

    rel_train, rel_val, rel_test = split_list(relevant, train_ratio, val_ratio)
    irr_train, irr_val, irr_test = split_list(irrelevant, train_ratio, val_ratio)

    train = rel_train + irr_train
    val = rel_val + irr_val
    test = rel_test + irr_test

    random.shuffle(train)
    random.shuffle(val)
    random.shuffle(test)

    return train, val, test


def save_jsonl(items: list[dict], path: Path):
    """Save items as JSONL."""
    with open(path, "w") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Export training data from news-aggregator")
    parser.add_argument("--dry-run", action="store_true", help="Show stats only, don't export")
    parser.add_argument("--output", type=str, default="data/final", help="Output directory")
    args = parser.parse_args()

    print("=" * 60)
    print("Export Training Data from News-Aggregator")
    print("=" * 60)

    # Fetch all items (relevant + irrelevant)
    print("\nFetching items from API...")
    all_items = fetch_all_items(relevant_only=False)

    if not all_items:
        print("No items found!")
        sys.exit(1)

    # Convert to training format
    print("\nConverting to training format...")
    training_data = [convert_to_training_format(item) for item in all_items]

    # Stats
    relevant = [i for i in training_data if i["labels"]["relevant"]]
    irrelevant = [i for i in training_data if not i["labels"]["relevant"]]

    print(f"\n{'='*60}")
    print("STATISTICS")
    print(f"{'='*60}")
    print(f"Total items:     {len(training_data)}")
    print(f"Relevant:        {len(relevant)} ({len(relevant)/len(training_data)*100:.1f}%)")
    print(f"Irrelevant:      {len(irrelevant)} ({len(irrelevant)/len(training_data)*100:.1f}%)")

    # Priority distribution (relevant only)
    priority_dist = Counter(i["labels"]["priority"] for i in relevant)
    print(f"\nPriority distribution (relevant only):")
    for p in PRIORITY_LEVELS:
        count = priority_dist.get(p, 0)
        print(f"  {p}: {count}")

    # AK distribution (relevant only)
    ak_dist = Counter(i["labels"]["ak"] for i in relevant if i["labels"]["ak"])
    print(f"\nAK distribution (relevant only):")
    for ak, count in sorted(ak_dist.items(), key=lambda x: -x[1]):
        print(f"  {ak}: {count}")

    # Multi-label stats
    multi_ak_items = [i for i in relevant if len(i["labels"].get("aks", [])) > 1]
    print(f"\nMulti-label stats:")
    print(f"  Items with multiple AKs: {len(multi_ak_items)} ({len(multi_ak_items)/len(relevant)*100:.1f}% of relevant)")

    if args.dry_run:
        print("\n[DRY RUN] No files written")
        return

    # Create splits
    print("\nCreating train/val/test splits (70/15/15, stratified)...")
    train, val, test = create_splits(training_data)

    # Save
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    save_jsonl(train, output_dir / "train.jsonl")
    save_jsonl(val, output_dir / "validation.jsonl")
    save_jsonl(test, output_dir / "test.jsonl")

    print(f"\nSaved to {output_dir}/:")
    print(f"  train.jsonl:      {len(train)} items")
    print(f"  validation.jsonl: {len(val)} items")
    print(f"  test.jsonl:       {len(test)} items")

    # Save stats
    stats = {
        "total": len(training_data),
        "relevant": len(relevant),
        "irrelevant": len(irrelevant),
        "train_size": len(train),
        "validation_size": len(val),
        "test_size": len(test),
        "by_ak": dict(ak_dist),
        "by_priority": dict(priority_dist),
        "created_at": datetime.now().isoformat(),
        "source": "news-aggregator-export"
    }

    with open(output_dir / "stats.json", "w") as f:
        json.dump(stats, f, indent=2)
    print(f"  stats.json:       metadata")

    print(f"\n{'='*60}")
    print("DONE! Ready for training:")
    print(f"  EMBEDDING_BACKEND=nomic-v2 python train_embedding_classifier.py")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
