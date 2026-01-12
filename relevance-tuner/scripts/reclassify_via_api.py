#!/usr/bin/env python3
"""
Reclassify items using the classifier API on gpu1:8082.

Fetches items from local backend, classifies via classifier API,
and shows comparison with existing classifications.

Usage:
    python scripts/reclassify_via_api.py              # Dry run - compare only
    python scripts/reclassify_via_api.py --apply      # Apply changes to metadata
    python scripts/reclassify_via_api.py --limit 50   # Process first 50 items
"""

import argparse
import json
import requests
from collections import Counter

# API endpoints
BACKEND_API = "http://localhost:8000"
CLASSIFIER_API = "http://localhost:8082"


def fetch_all_items(limit: int = None) -> list[dict]:
    """Fetch all items from backend."""
    items = []
    page = 1
    page_size = 100

    while True:
        url = f"{BACKEND_API}/api/items?page={page}&page_size={page_size}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        items.extend(data["items"])

        if limit and len(items) >= limit:
            items = items[:limit]
            break

        if len(data["items"]) < page_size:
            break
        page += 1

    return items


def classify_item(title: str, content: str, source: str = "") -> dict:
    """Classify a single item via classifier API."""
    resp = requests.post(
        f"{CLASSIFIER_API}/classify",
        json={"title": title, "content": content, "source": source},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_existing_classification(item: dict) -> dict:
    """Extract existing classification from item metadata."""
    metadata = item.get("metadata_", {}) or {}
    llm_analysis = metadata.get("llm_analysis", {}) or {}
    pre_filter = metadata.get("pre_filter", {}) or {}

    return {
        "priority": item.get("priority"),
        "llm_priority": llm_analysis.get("priority_suggestion"),
        "llm_ak": llm_analysis.get("assigned_ak"),
        "prefilter_ak": pre_filter.get("ak_suggestion"),
    }


def main():
    parser = argparse.ArgumentParser(description="Reclassify items via classifier API")
    parser.add_argument("--limit", type=int, help="Limit number of items to process")
    parser.add_argument("--apply", action="store_true", help="Apply changes (not implemented yet)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show each item")
    args = parser.parse_args()

    # Check classifier health
    print("Checking classifier API...")
    try:
        health = requests.get(f"{CLASSIFIER_API}/health", timeout=5).json()
        print(f"  Classifier: {health['model']}, GPU: {health['gpu']}")
    except Exception as e:
        print(f"  ERROR: Classifier not available: {e}")
        return

    # Fetch items
    print(f"\nFetching items from backend...")
    items = fetch_all_items(limit=args.limit)
    print(f"  Fetched {len(items)} items")

    # Classify each item
    print(f"\nClassifying items...")

    stats = {
        "relevance_match": 0,
        "relevance_mismatch": 0,
        "ak_match": 0,
        "ak_mismatch": 0,
        "ak_new": 0,
    }
    ak_changes = Counter()
    priority_changes = Counter()
    mismatches = []

    for i, item in enumerate(items):
        if (i + 1) % 50 == 0:
            print(f"  Processed {i + 1}/{len(items)}...")

        try:
            # Get new classification
            result = classify_item(
                title=item["title"],
                content=item.get("content", ""),
                source=item.get("source", {}).get("name", "") if item.get("source") else "",
            )

            # Get existing classification
            existing = get_existing_classification(item)

            # Compare relevance
            existing_relevant = existing["priority"] not in ["low", None]
            new_relevant = result["relevant"]

            if existing_relevant == new_relevant:
                stats["relevance_match"] += 1
            else:
                stats["relevance_mismatch"] += 1
                if args.verbose:
                    print(f"  Relevance mismatch: {item['title'][:50]}...")
                    print(f"    Existing: {existing['priority']}, New: {'relevant' if new_relevant else 'irrelevant'} ({result['relevance_confidence']:.1%})")

            # Compare AK
            existing_ak = existing.get("llm_ak") or existing.get("prefilter_ak")
            new_ak = result.get("ak")

            if existing_ak and new_ak:
                if existing_ak == new_ak:
                    stats["ak_match"] += 1
                else:
                    stats["ak_mismatch"] += 1
                    ak_changes[f"{existing_ak} -> {new_ak}"] += 1
                    mismatches.append({
                        "title": item["title"][:60],
                        "old_ak": existing_ak,
                        "new_ak": new_ak,
                        "confidence": result.get("ak_confidence", 0),
                    })
            elif new_ak and not existing_ak:
                stats["ak_new"] += 1

            # Compare priority
            if existing["priority"] != result.get("priority"):
                priority_changes[f"{existing['priority']} -> {result.get('priority')}"] += 1

        except Exception as e:
            print(f"  Error classifying item {item['id']}: {e}")

    # Print summary
    print("\n" + "=" * 60)
    print("CLASSIFICATION COMPARISON SUMMARY")
    print("=" * 60)

    print(f"\nRelevance:")
    print(f"  Match:    {stats['relevance_match']:3d} ({stats['relevance_match']/len(items)*100:.1f}%)")
    print(f"  Mismatch: {stats['relevance_mismatch']:3d} ({stats['relevance_mismatch']/len(items)*100:.1f}%)")

    print(f"\nAK Classification:")
    print(f"  Match:    {stats['ak_match']:3d}")
    print(f"  Mismatch: {stats['ak_mismatch']:3d}")
    print(f"  New:      {stats['ak_new']:3d} (no existing AK)")

    if ak_changes:
        print(f"\nAK Changes:")
        for change, count in ak_changes.most_common(10):
            print(f"  {change}: {count}")

    if priority_changes:
        print(f"\nPriority Changes:")
        for change, count in priority_changes.most_common(10):
            print(f"  {change}: {count}")

    if mismatches and args.verbose:
        print(f"\nAK Mismatches (showing first 10):")
        for m in mismatches[:10]:
            print(f"  {m['old_ak']} -> {m['new_ak']} ({m['confidence']:.1%}): {m['title']}")

    if args.apply:
        print("\n--apply not implemented yet. Would update item metadata.")


if __name__ == "__main__":
    main()
