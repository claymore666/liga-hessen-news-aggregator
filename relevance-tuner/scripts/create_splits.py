#!/usr/bin/env python3
"""
Create train/validation/test splits from Ollama-labeled data.

Usage:
    python scripts/create_splits.py
    python scripts/create_splits.py --include-old  # Merge with old data
"""

import argparse
import json
import random
from collections import Counter
from datetime import datetime
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
OLLAMA_RESULTS = PROJECT_ROOT / "data" / "reviewed" / "ollama_results"
OLD_DATA_DIR = PROJECT_ROOT / "data" / "final"
OUTPUT_DIR = PROJECT_ROOT / "data" / "final"

# Split ratios
TRAIN_RATIO = 0.70
VAL_RATIO = 0.15
TEST_RATIO = 0.15

RANDOM_SEED = 42


def load_ollama_results() -> list[dict]:
    """Load all Ollama-labeled results."""
    items = []
    for f in sorted(OLLAMA_RESULTS.glob("batch_*_labeled.jsonl")):
        with open(f, "r", encoding="utf-8") as fp:
            for line in fp:
                if line.strip():
                    item = json.loads(line)
                    # Skip failed labelings
                    if item["labels"]["relevant"] is not None:
                        items.append(item)
    return items


def load_old_data() -> list[dict]:
    """Load old data from all splits."""
    items = []
    for split in ["train", "validation", "test"]:
        path = OLD_DATA_DIR / f"{split}.jsonl"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        items.append(json.loads(line))
    return items


def deduplicate(items: list[dict]) -> list[dict]:
    """Remove duplicates based on title."""
    seen_titles = set()
    unique = []
    for item in items:
        title = item["input"]["title"].strip().lower()
        if title not in seen_titles:
            seen_titles.add(title)
            unique.append(item)
    return unique


def stratified_split(items: list[dict], train_ratio: float, val_ratio: float, seed: int):
    """Split items while maintaining class balance (relevant/irrelevant)."""
    random.seed(seed)

    # Separate by class
    relevant = [i for i in items if i["labels"]["relevant"] is True]
    irrelevant = [i for i in items if i["labels"]["relevant"] is False]

    # Shuffle
    random.shuffle(relevant)
    random.shuffle(irrelevant)

    def split_list(lst, train_r, val_r):
        n = len(lst)
        train_end = int(n * train_r)
        val_end = int(n * (train_r + val_r))
        return lst[:train_end], lst[train_end:val_end], lst[val_end:]

    # Split each class
    rel_train, rel_val, rel_test = split_list(relevant, train_ratio, val_ratio)
    irr_train, irr_val, irr_test = split_list(irrelevant, train_ratio, val_ratio)

    # Combine and shuffle
    train = rel_train + irr_train
    val = rel_val + irr_val
    test = rel_test + irr_test

    random.shuffle(train)
    random.shuffle(val)
    random.shuffle(test)

    return train, val, test


def compute_stats(items: list[dict]) -> dict:
    """Compute statistics for a dataset."""
    relevant = [i for i in items if i["labels"]["relevant"] is True]
    irrelevant = [i for i in items if i["labels"]["relevant"] is False]

    ak_counts = Counter(i["labels"]["ak"] for i in relevant if i["labels"]["ak"])
    priority_counts = Counter(i["labels"]["priority"] for i in relevant if i["labels"]["priority"])

    return {
        "total": len(items),
        "relevant": len(relevant),
        "irrelevant": len(irrelevant),
        "by_ak": dict(ak_counts),
        "by_priority": dict(priority_counts),
    }


def save_jsonl(items: list[dict], path: Path):
    """Save items to JSONL file."""
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Create train/val/test splits")
    parser.add_argument("--include-old", action="store_true", help="Include old labeled data")
    args = parser.parse_args()

    print("=" * 60)
    print("Creating Training Data Splits")
    print("=" * 60)

    # Load Ollama results
    print("\n[1/4] Loading Ollama-labeled data...")
    items = load_ollama_results()
    print(f"  Loaded: {len(items)} items")

    # Optionally include old data
    if args.include_old:
        print("\n[1b] Loading old labeled data...")
        old_items = load_old_data()
        print(f"  Old data: {len(old_items)} items")
        items = items + old_items

    # Deduplicate
    print("\n[2/4] Deduplicating...")
    before = len(items)
    items = deduplicate(items)
    print(f"  Removed {before - len(items)} duplicates")
    print(f"  Unique items: {len(items)}")

    # Split
    print("\n[3/4] Creating stratified splits...")
    train, val, test = stratified_split(items, TRAIN_RATIO, VAL_RATIO, RANDOM_SEED)

    print(f"  Train: {len(train)} ({len(train)/len(items)*100:.1f}%)")
    print(f"  Validation: {len(val)} ({len(val)/len(items)*100:.1f}%)")
    print(f"  Test: {len(test)} ({len(test)/len(items)*100:.1f}%)")

    # Stats
    train_stats = compute_stats(train)
    val_stats = compute_stats(val)
    test_stats = compute_stats(test)
    total_stats = compute_stats(items)

    print("\n  Class balance:")
    for name, split, stats in [("Train", train, train_stats), ("Val", val, val_stats), ("Test", test, test_stats)]:
        rel_pct = stats["relevant"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"    {name}: {stats['relevant']} relevant ({rel_pct:.1f}%), {stats['irrelevant']} irrelevant")

    # Save
    print("\n[4/4] Saving splits...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    save_jsonl(train, OUTPUT_DIR / "train.jsonl")
    save_jsonl(val, OUTPUT_DIR / "validation.jsonl")
    save_jsonl(test, OUTPUT_DIR / "test.jsonl")

    # Save stats
    stats = {
        "total": len(items),
        "relevant": total_stats["relevant"],
        "irrelevant": total_stats["irrelevant"],
        "train_size": len(train),
        "validation_size": len(val),
        "test_size": len(test),
        "by_ak": total_stats["by_ak"],
        "by_priority": total_stats["by_priority"],
        "created_at": datetime.now().isoformat(),
        "source": "ollama_qwen3:14b-q8_0" + (" + old_data" if args.include_old else ""),
    }
    with open(OUTPUT_DIR / "stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    print(f"\n  Saved to: {OUTPUT_DIR}")
    print("=" * 60)
    print("Done!")
    print(f"\nTotal: {len(items)} items")
    print(f"  Relevant: {total_stats['relevant']} ({total_stats['relevant']/len(items)*100:.1f}%)")
    print(f"  Irrelevant: {total_stats['irrelevant']} ({total_stats['irrelevant']/len(items)*100:.1f}%)")
    print(f"\nAK distribution: {total_stats['by_ak']}")
    print(f"Priority distribution: {total_stats['by_priority']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
