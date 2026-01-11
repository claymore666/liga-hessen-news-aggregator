#!/usr/bin/env python3
"""Incremental training data update script.

This script:
1. Refetches specified sources (RSS, etc.) to get improved content
2. Matches new items against existing labels by title
3. Identifies items that need relabeling (content improved)
4. Exports only items needing labels
5. After labeling, merges with existing data
6. Selects final training data: 100% long + 10% short
"""

import argparse
import asyncio
import hashlib
import json
import random
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
LABELED_DIR = DATA_DIR / "reviewed" / "ollama_results"
RAW_DIR = DATA_DIR / "raw"
FINAL_DIR = DATA_DIR / "final"

API_URL = "http://localhost:8000"


def load_existing_labels() -> dict[str, dict]:
    """Load all existing labeled data, indexed by normalized title."""
    labels = {}

    for batch_file in sorted(LABELED_DIR.glob("batch_*_labeled.jsonl")):
        with open(batch_file) as f:
            for line in f:
                try:
                    item = json.loads(line)
                    title = item.get("input", {}).get("title", "")
                    normalized = normalize_title(title)
                    if normalized:
                        # Keep the one with longer content if duplicate
                        existing = labels.get(normalized)
                        if existing:
                            existing_len = len(existing.get("input", {}).get("content", ""))
                            new_len = len(item.get("input", {}).get("content", ""))
                            if new_len > existing_len:
                                labels[normalized] = item
                        else:
                            labels[normalized] = item
                except json.JSONDecodeError:
                    continue

    print(f"Loaded {len(labels)} existing labeled items")
    return labels


def normalize_title(title: str) -> str:
    """Normalize title for matching."""
    if not title:
        return ""
    # Remove special chars, lowercase, collapse whitespace
    normalized = re.sub(r'[^\w\s]', '', title.lower())
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized[:100]  # First 100 chars for matching


def fetch_sources(source_ids: list[int], training_mode: bool = True) -> list[dict]:
    """Fetch items from specified sources via API."""
    items = []

    for sid in source_ids:
        print(f"Fetching source {sid}...", end=" ", flush=True)
        try:
            url = f"{API_URL}/api/sources/{sid}/fetch"
            if training_mode:
                url += "?training_mode=true"

            result = subprocess.run(
                ["curl", "-s", "-X", "POST", url],
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                count = data.get("items_collected", 0)
                print(f"{count} items")
            else:
                print(f"error")
        except Exception as e:
            print(f"failed: {e}")

    return items


def export_items_from_sources(source_ids: list[int]) -> list[dict]:
    """Export items from DB for specified sources."""
    items = []

    # Get all items from API
    print("Exporting items from API...")
    result = subprocess.run(
        ["curl", "-s", f"{API_URL}/api/items?page_size=2000"],
        capture_output=True,
        text=True,
        timeout=60
    )

    if result.returncode != 0:
        print("Failed to fetch items from API")
        return items

    data = json.loads(result.stdout)
    all_items = data.get("items", [])

    # Filter by source - need to map source names to IDs
    # For now, just return all items and filter later
    print(f"Got {len(all_items)} total items from API")

    return all_items


def identify_items_to_relabel(
    existing_labels: dict[str, dict],
    new_items: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    """Identify which items need relabeling.

    Returns:
        - items_to_relabel: existing items with improved content
        - new_items_to_label: completely new items
        - items_to_keep: existing items with same/worse content
    """
    items_to_relabel = []
    new_items_to_label = []
    items_to_keep = []

    for item in new_items:
        title = item.get("title", "")
        content = item.get("content", "")
        normalized = normalize_title(title)

        if not normalized:
            continue

        existing = existing_labels.get(normalized)

        if existing:
            existing_content = existing.get("input", {}).get("content", "")

            # Check if new content is significantly longer (>20% improvement)
            if len(content) > len(existing_content) * 1.2 and len(content) >= 500:
                items_to_relabel.append({
                    "item": item,
                    "old_len": len(existing_content),
                    "new_len": len(content),
                    "existing_label": existing
                })
            else:
                items_to_keep.append(existing)
        else:
            new_items_to_label.append(item)

    print(f"\nAnalysis:")
    print(f"  Items to relabel (content improved): {len(items_to_relabel)}")
    print(f"  New items to label: {len(new_items_to_label)}")
    print(f"  Items to keep (no change): {len(items_to_keep)}")

    return items_to_relabel, new_items_to_label, items_to_keep


def create_labeling_batch(items: list[dict], output_path: Path):
    """Create a batch file for labeling."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        for item in items:
            # Convert API item format to labeling format
            record = {
                "input": {
                    "title": item.get("title", ""),
                    "content": item.get("content", ""),
                    "source": item.get("source_name", "Unknown"),
                    "date": item.get("published_at", "")[:10] if item.get("published_at") else ""
                },
                "labels": {
                    "relevant": None,
                    "priority": None,
                    "ak": None,
                    "reaction_type": None
                },
                "provenance": {
                    "source_type": "news",
                    "reasoning": None,
                    "affected_groups": []
                }
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Created labeling batch: {output_path} ({len(items)} items)")


def select_training_data(
    all_labeled: list[dict],
    long_pct: float = 1.0,
    short_pct: float = 0.1,
    min_content_len: int = 500
) -> list[dict]:
    """Select training data: 100% of long content + 10% of short.

    Args:
        all_labeled: All labeled items
        long_pct: Percentage of long items to include (default 100%)
        short_pct: Percentage of short items to include (default 10%)
        min_content_len: Threshold for long vs short

    Returns:
        Selected items for training
    """
    long_items = []
    short_items = []

    for item in all_labeled:
        content = item.get("input", {}).get("content", "")
        if len(content) >= min_content_len:
            long_items.append(item)
        else:
            short_items.append(item)

    # Select items
    selected_long = random.sample(long_items, int(len(long_items) * long_pct))
    selected_short = random.sample(short_items, int(len(short_items) * short_pct))

    selected = selected_long + selected_short
    random.shuffle(selected)

    print(f"\nTraining data selection:")
    print(f"  Long items (>={min_content_len} chars): {len(selected_long)}/{len(long_items)} ({long_pct*100:.0f}%)")
    print(f"  Short items (<{min_content_len} chars): {len(selected_short)}/{len(short_items)} ({short_pct*100:.0f}%)")
    print(f"  Total selected: {len(selected)}")

    return selected


def merge_labels(
    existing_labels: dict[str, dict],
    new_labels_file: Path,
    items_to_keep: list[dict]
) -> list[dict]:
    """Merge new labels with existing ones."""
    all_labels = list(items_to_keep)

    # Add new labels
    if new_labels_file.exists():
        with open(new_labels_file) as f:
            for line in f:
                try:
                    item = json.loads(line)
                    all_labels.append(item)
                except json.JSONDecodeError:
                    continue

    print(f"Merged labels: {len(all_labels)} total items")
    return all_labels


def save_final_data(items: list[dict], output_dir: Path):
    """Save final training data with train/val/test splits."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Shuffle
    random.shuffle(items)

    # Split 70/15/15
    n = len(items)
    train_end = int(n * 0.7)
    val_end = int(n * 0.85)

    splits = {
        "train": items[:train_end],
        "val": items[train_end:val_end],
        "test": items[val_end:]
    }

    for name, data in splits.items():
        path = output_dir / f"{name}.jsonl"
        with open(path, "w") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"Saved {path}: {len(data)} items")


def main():
    parser = argparse.ArgumentParser(description="Incremental training data update")
    parser.add_argument("--refetch", action="store_true", help="Refetch RSS sources")
    parser.add_argument("--analyze", action="store_true", help="Analyze what needs relabeling")
    parser.add_argument("--export-batch", type=str, help="Export items needing labels to batch file")
    parser.add_argument("--merge", type=str, help="Merge new labels from file")
    parser.add_argument("--select", action="store_true", help="Select final training data")
    parser.add_argument("--short-pct", type=float, default=0.1, help="Percentage of short items (default 10%%)")
    parser.add_argument("--source-ids", type=str, help="Comma-separated source IDs to refetch")

    args = parser.parse_args()

    # Default RSS source IDs
    RSS_SOURCE_IDS = [160, 161, 162, 163, 164, 165, 166, 167, 172, 187, 232, 233, 234, 235]

    if args.source_ids:
        RSS_SOURCE_IDS = [int(x) for x in args.source_ids.split(",")]

    if args.refetch:
        print("=== Refetching RSS sources ===")
        fetch_sources(RSS_SOURCE_IDS, training_mode=True)
        print("\nRefetch complete. Run with --analyze to see what needs relabeling.")

    elif args.analyze:
        print("=== Analyzing items for relabeling ===")
        existing = load_existing_labels()
        new_items = export_items_from_sources(RSS_SOURCE_IDS)

        to_relabel, to_label, to_keep = identify_items_to_relabel(existing, new_items)

        if to_relabel:
            print("\nItems with improved content:")
            for item in to_relabel[:5]:
                print(f"  {item['item'].get('title', '')[:50]}...")
                print(f"    {item['old_len']} -> {item['new_len']} chars")

    elif args.export_batch:
        print("=== Exporting items for labeling ===")
        existing = load_existing_labels()
        new_items = export_items_from_sources(RSS_SOURCE_IDS)

        to_relabel, to_label, _ = identify_items_to_relabel(existing, new_items)

        # Combine items needing labels
        items_for_labeling = [x["item"] for x in to_relabel] + to_label

        if items_for_labeling:
            create_labeling_batch(items_for_labeling, Path(args.export_batch))
        else:
            print("No items need labeling!")

    elif args.merge:
        print("=== Merging labels ===")
        existing = load_existing_labels()
        new_items = export_items_from_sources(RSS_SOURCE_IDS)

        _, _, to_keep = identify_items_to_relabel(existing, new_items)

        all_labels = merge_labels(existing, Path(args.merge), to_keep)

        # Save merged data
        merged_path = LABELED_DIR / "merged_labels.jsonl"
        with open(merged_path, "w") as f:
            for item in all_labels:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"Saved merged labels to {merged_path}")

    elif args.select:
        print("=== Selecting final training data ===")

        # Load all labels
        all_labels = []
        for batch_file in sorted(LABELED_DIR.glob("batch_*_labeled.jsonl")):
            with open(batch_file) as f:
                for line in f:
                    try:
                        all_labels.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        # Also check for merged labels
        merged_path = LABELED_DIR / "merged_labels.jsonl"
        if merged_path.exists():
            print(f"Using merged labels from {merged_path}")
            all_labels = []
            with open(merged_path) as f:
                for line in f:
                    try:
                        all_labels.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        print(f"Loaded {len(all_labels)} labeled items")

        # Select training data
        selected = select_training_data(all_labels, short_pct=args.short_pct)

        # Save final data
        save_final_data(selected, FINAL_DIR)

    else:
        parser.print_help()
        print("\n\nWorkflow:")
        print("  1. python incremental_update.py --refetch")
        print("  2. python incremental_update.py --analyze")
        print("  3. python incremental_update.py --export-batch data/raw/relabel_batch.jsonl")
        print("  4. python label_with_ollama.py --batch data/raw/relabel_batch.jsonl")
        print("  5. python incremental_update.py --merge data/reviewed/ollama_results/relabel_batch_labeled.jsonl")
        print("  6. python incremental_update.py --select --short-pct 0.1")


if __name__ == "__main__":
    main()
