#!/usr/bin/env python3
"""Export training data from news-aggregator database for embedding classifier.

This script exports LLM-curated items directly from the production database.
No re-labeling needed - items already have relevance, priority, and AK labels.

Usage:
    python scripts/export_training_data.py              # Export all items
    python scripts/export_training_data.py --dry-run   # Show stats only
    python scripts/export_training_data.py --output data/final  # Custom output dir

Filtering options (recommended for better training data quality):
    --min-content-length 200   # Skip items with less than 200 chars content
    --min-confidence 0.6       # Skip items with LLM relevance score < 0.6

Note: Eurostat dataset notifications often have minimal content (~139 chars) and
contribute noise to training data. Using --min-content-length 200 filters these out.

Items not yet processed by the LLM are always excluded (needs_llm_processing=true
or no summary). Classifier-vs-LLM disagreements are automatically fetched and
forced into the training split for maximum learning signal.
"""

import argparse
import json
import os
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request

# Configuration
API_URL = os.environ.get("API_URL", "http://localhost:8000/api")
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


def fetch_disagreement_ids() -> set[int]:
    """Fetch all item IDs where classifier and LLM disagree on priority or AK."""
    ids = set()
    offset = 0
    limit = 200  # Max allowed by API

    while True:
        url = f"{API_URL}/analytics/disagreements?limit={limit}&offset={offset}"
        try:
            req = Request(url)
            with urlopen(req, timeout=30) as resp:
                data = json.load(resp)
                if not data:
                    break
                for item in data:
                    ids.add(item["id"])
                print(f"  Disagreements page {offset // limit + 1}: {len(data)} items")
                if len(data) < limit:
                    break
                offset += limit
        except Exception as e:
            print(f"Warning: Could not fetch disagreements: {e}")
            break

    return ids


def convert_to_training_format(
    item: dict,
    min_content_length: int = 0,
    min_confidence: float = 0.0,
    disagreement_ids: set[int] | None = None,
) -> tuple[dict | None, str | None]:
    """Convert API item to training data format.

    Args:
        item: API item dict
        min_content_length: Minimum content length (chars). Items below are skipped.
        min_confidence: Minimum LLM confidence score (0.0-1.0). Items below are skipped.
        disagreement_ids: Set of item IDs where classifier and LLM disagree.

    Returns:
        Tuple of (training data dict or None, filter reason or None).
    """
    # Filter out items not yet processed by LLM
    if item.get("needs_llm_processing", True) or not item.get("summary"):
        return None, "no_llm"

    content = item.get("content", "")

    # Filter by content length
    if min_content_length > 0 and len(content) < min_content_length:
        return None, "quality"

    # Filter by LLM confidence score
    if min_confidence > 0:
        metadata = item.get("metadata", {})
        llm_analysis = metadata.get("llm_analysis", {})
        relevance_score = llm_analysis.get("relevance_score", 0.0)
        if relevance_score < min_confidence:
            return None, "quality"

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

    item_id = item.get("id")
    is_disagreement = bool(disagreement_ids and item_id in disagreement_ids)

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
            "item_id": item_id,
            "is_disagreement": is_disagreement,
            "exported_at": datetime.now().isoformat()
        }
    }, None


def create_splits(
    items: list[dict],
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
    disagreement_ids: set[int] | None = None,
):
    """Create stratified train/val/test splits.

    Disagreement items (where classifier got it wrong vs LLM) are forced into the
    training split so the classifier learns from its mistakes. Non-disagreement items
    get the standard stratified 70/15/15 split.
    """
    random.seed(RANDOM_SEED)

    # Separate disagreements (always go to training)
    if disagreement_ids:
        disagreements = [i for i in items if i["provenance"].get("item_id") in disagreement_ids]
        non_disagreements = [i for i in items if i["provenance"].get("item_id") not in disagreement_ids]
    else:
        disagreements = []
        non_disagreements = items

    # Stratified split on non-disagreement items only
    relevant = [i for i in non_disagreements if i["labels"]["relevant"]]
    irrelevant = [i for i in non_disagreements if not i["labels"]["relevant"]]

    random.shuffle(relevant)
    random.shuffle(irrelevant)

    def split_list(lst, train_r, val_r):
        n = len(lst)
        train_end = int(n * train_r)
        val_end = int(n * (train_r + val_r))
        return lst[:train_end], lst[train_end:val_end], lst[val_end:]

    rel_train, rel_val, rel_test = split_list(relevant, train_ratio, val_ratio)
    irr_train, irr_val, irr_test = split_list(irrelevant, train_ratio, val_ratio)

    # All disagreements go to training
    train = rel_train + irr_train + disagreements
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
    parser.add_argument(
        "--min-content-length", type=int, default=0,
        help="Minimum content length in chars (recommended: 200 to filter Eurostat noise)"
    )
    parser.add_argument(
        "--min-confidence", type=float, default=0.0,
        help="Minimum LLM relevance score 0.0-1.0 (recommended: 0.6 for higher quality labels)"
    )
    parser.add_argument(
        "--haiku-overrides", type=str, default=None,
        help="JSONL file with Haiku-labeled items (from label_disagreements.py). "
             "Overrides LLM labels for matching item IDs with higher-quality Haiku labels."
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Export Training Data from News-Aggregator")
    print("=" * 60)
    print(f"API: {API_URL}")

    # Show filter settings
    if args.min_content_length > 0 or args.min_confidence > 0:
        print("\nFilters:")
        if args.min_content_length > 0:
            print(f"  Min content length: {args.min_content_length} chars")
        if args.min_confidence > 0:
            print(f"  Min LLM confidence: {args.min_confidence}")

    # Fetch all items (relevant + irrelevant)
    print("\nFetching items from API...")
    all_items = fetch_all_items(relevant_only=False)

    if not all_items:
        print("No items found!")
        sys.exit(1)

    # Fetch classifier-vs-LLM disagreements
    print("\nFetching classifier-LLM disagreements...")
    disagreement_ids = fetch_disagreement_ids()
    print(f"  Found {len(disagreement_ids)} disagreement items")

    # Convert to training format (with filtering)
    print("\nConverting to training format...")
    training_data = []
    llm_filtered = 0
    quality_filtered = 0
    for item in all_items:
        converted, reason = convert_to_training_format(
            item,
            min_content_length=args.min_content_length,
            min_confidence=args.min_confidence,
            disagreement_ids=disagreement_ids,
        )
        if converted is not None:
            training_data.append(converted)
        elif reason == "no_llm":
            llm_filtered += 1
        else:
            quality_filtered += 1

    if llm_filtered > 0:
        print(f"  Excluded: {llm_filtered} items without LLM processing")
    if quality_filtered > 0:
        print(f"  Excluded: {quality_filtered} items below content/confidence thresholds")

    # Apply Haiku label overrides (higher-quality labels for disagreement items)
    haiku_overrides_applied = 0
    if args.haiku_overrides:
        haiku_path = Path(args.haiku_overrides)
        if not haiku_path.exists():
            print(f"WARNING: Haiku overrides file not found: {haiku_path}")
        else:
            # Load Haiku-labeled items keyed by item_id
            haiku_by_id = {}
            with open(haiku_path) as f:
                for line in f:
                    try:
                        record = json.loads(line)
                        item_id = record.get("provenance", {}).get("item_id")
                        if item_id:
                            haiku_by_id[item_id] = record
                    except json.JSONDecodeError:
                        pass
            print(f"\nApplying Haiku label overrides ({len(haiku_by_id)} items)...")

            # Replace labels for matching items in training data
            for i, item in enumerate(training_data):
                item_id = item["provenance"].get("item_id")
                if item_id in haiku_by_id:
                    haiku_record = haiku_by_id.pop(item_id)
                    training_data[i]["labels"] = haiku_record["labels"]
                    training_data[i]["provenance"]["label_source"] = "haiku"
                    training_data[i]["provenance"]["is_disagreement"] = True
                    haiku_overrides_applied += 1

            # Add Haiku items that weren't in training data (e.g., filtered by quality)
            haiku_added = 0
            existing_ids = {item["provenance"].get("item_id") for item in training_data}
            for item_id, haiku_record in haiku_by_id.items():
                if item_id not in existing_ids:
                    training_data.append(haiku_record)
                    haiku_added += 1

            print(f"  Overridden: {haiku_overrides_applied} items")
            if haiku_added > 0:
                print(f"  Added:      {haiku_added} items (not in API export)")

    if not training_data:
        print("No training data after filtering!")
        sys.exit(1)

    # Stats
    relevant = [i for i in training_data if i["labels"]["relevant"]]
    irrelevant = [i for i in training_data if not i["labels"]["relevant"]]

    print(f"\n{'='*60}")
    print("STATISTICS")
    print(f"{'='*60}")
    print(f"Total items:     {len(training_data)}")
    print(f"Relevant:        {len(relevant)} ({len(relevant)/len(training_data)*100:.1f}%)")
    print(f"Irrelevant:      {len(irrelevant)} ({len(irrelevant)/len(training_data)*100:.1f}%)")

    # Disagreement stats
    disagreement_items = [i for i in training_data if i["provenance"].get("is_disagreement")]
    haiku_labeled = [i for i in training_data if i["provenance"].get("label_source") == "haiku"]
    print(f"\nClassifier-LLM disagreements:")
    print(f"  Found:           {len(disagreement_ids)}")
    print(f"  In training data: {len(disagreement_items)} (after filters)")
    if len(disagreement_ids) > len(disagreement_items):
        print(f"  Excluded:         {len(disagreement_ids) - len(disagreement_items)} (filtered by content/confidence)")
    if haiku_labeled:
        print(f"  Haiku-labeled:    {len(haiku_labeled)} (higher-quality ground truth)")

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

    # Create splits (disagreements forced into training)
    print(f"\nCreating train/val/test splits (70/15/15, stratified)...")
    print(f"  ({len(disagreement_items)} disagreement items forced into training split)")
    train, val, test = create_splits(training_data, disagreement_ids=disagreement_ids)

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
        "filtered_no_llm": llm_filtered,
        "filtered_quality": quality_filtered,
        "relevant": len(relevant),
        "irrelevant": len(irrelevant),
        "disagreements_found": len(disagreement_ids),
        "disagreements_in_training": len(disagreement_items),
        "train_size": len(train),
        "validation_size": len(val),
        "test_size": len(test),
        "by_ak": dict(ak_dist),
        "by_priority": dict(priority_dist),
        "filters": {
            "min_content_length": args.min_content_length,
            "min_confidence": args.min_confidence
        },
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
    if args.min_content_length == 0 and args.min_confidence == 0.0:
        print("\nTip: For higher quality training data, consider using filters:")
        print("  --min-content-length 200 --min-confidence 0.6")


if __name__ == "__main__":
    main()
