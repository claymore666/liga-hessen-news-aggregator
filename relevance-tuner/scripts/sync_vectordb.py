#!/usr/bin/env python3
"""
Sync LLM-processed items from PostgreSQL to ChromaDB vector store.

The vector store upserts items (no duplicates) - safe to run multiple times.

Usage:
    python scripts/sync_vectordb.py              # Sync all LLM-processed items
    python scripts/sync_vectordb.py --dry-run    # Show what would be synced
    python scripts/sync_vectordb.py --ak AK3     # Only sync specific AK
"""

import argparse
import json
import sys
from urllib.request import urlopen, Request

# Configuration
NEWS_API_URL = "http://localhost:8000/api"
CLASSIFIER_URL = "http://gpu1:8082"
BATCH_SIZE = 50


def get_vectordb_stats() -> dict:
    """Get vector store stats."""
    try:
        req = Request(f"{CLASSIFIER_URL}/health")
        with urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except Exception as e:
        print(f"Error connecting to classifier: {e}")
        sys.exit(1)


def get_llm_processed_items(page: int = 1, page_size: int = 100, ak_filter: str = None) -> tuple[list, int]:
    """Get LLM-processed items from news-aggregator API."""
    try:
        url = f"{NEWS_API_URL}/items?page={page}&page_size={page_size}&relevant_only=false"
        if ak_filter:
            url += f"&assigned_ak={ak_filter}"

        req = Request(url)
        with urlopen(req, timeout=30) as resp:
            data = json.load(resp)
            items = data.get("items", [])
            total = data.get("total", 0)

            # Filter to only LLM-processed items
            processed = [
                item for item in items
                if item.get("metadata", {}).get("llm_analysis")
            ]
            return processed, total
    except Exception as e:
        print(f"Error fetching items: {e}")
        return [], 0


def index_batch(items: list) -> int:
    """Add items to vector store (upserts, no duplicates)."""
    if not items:
        return 0

    batch = []
    for item in items:
        source = item.get("source", {})
        batch.append({
            "id": str(item["id"]),
            "title": item.get("title", ""),
            "content": item.get("content", "")[:5000],
            "metadata": {
                "priority": item.get("priority", "none"),
                "assigned_ak": item.get("assigned_ak"),
                "source": source.get("name", "") if source else "",
                "channel_id": str(item.get("channel_id", "")),
            }
        })

    try:
        req = Request(
            f"{CLASSIFIER_URL}/index/batch",
            data=json.dumps({"items": batch}).encode(),
            headers={"Content-Type": "application/json"}
        )
        with urlopen(req, timeout=60) as resp:
            data = json.load(resp)
            return data.get("added", 0)
    except Exception as e:
        print(f"Error indexing batch: {e}")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Sync LLM-processed items to ChromaDB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced")
    parser.add_argument("--ak", type=str, help="Only sync specific AK (e.g., AK3)")
    args = parser.parse_args()

    print("=" * 60)
    print("Syncing LLM-processed items to ChromaDB")
    print("=" * 60)

    # Initial stats
    stats = get_vectordb_stats()
    print(f"Vector store: {stats.get('vector_store_items', 0)} items")

    # Collect all LLM-processed items
    all_items = []
    page = 1

    print(f"\nFetching LLM-processed items{f' (AK={args.ak})' if args.ak else ''}...")

    while True:
        items, total = get_llm_processed_items(page=page, ak_filter=args.ak)
        if not items:
            if page == 1:
                print("  No items found")
            break

        all_items.extend(items)
        print(f"  Page {page}: {len(items)} items (total: {len(all_items)})")

        if len(all_items) >= total:
            break
        page += 1

    print(f"\nFound {len(all_items)} LLM-processed items")

    # Show AK distribution
    ak_counts = {}
    for item in all_items:
        ak = item.get("assigned_ak") or "none"
        ak_counts[ak] = ak_counts.get(ak, 0) + 1

    print("\nAK distribution:")
    for ak, count in sorted(ak_counts.items(), key=lambda x: -x[1]):
        print(f"  {ak}: {count}")

    if args.dry_run:
        print("\n[DRY RUN] Would sync these items (no changes made)")
        return

    if not all_items:
        print("Nothing to sync!")
        return

    # Sync in batches (upsert handles duplicates)
    print(f"\nSyncing {len(all_items)} items in batches of {BATCH_SIZE}...")
    total_added = 0

    for i in range(0, len(all_items), BATCH_SIZE):
        batch = all_items[i:i + BATCH_SIZE]
        added = index_batch(batch)
        total_added += added
        print(f"  Batch {i // BATCH_SIZE + 1}: {added} new items")

    # Final stats
    stats = get_vectordb_stats()
    print(f"\nDone! Vector store now has {stats.get('vector_store_items', 0)} items")
    print(f"Added {total_added} new items (duplicates skipped)")


if __name__ == "__main__":
    main()
