#!/usr/bin/env python3
"""
Reclassify production items using sklearn classifiers.

Fetches items from production API, runs through relevance and AK classifiers,
and compares results with existing LLM classifications.

Usage:
    python scripts/reclassify_production.py              # Dry run - compare only
    python scripts/reclassify_production.py --apply      # Apply changes via API
    python scripts/reclassify_production.py --limit 100  # Process first 100 items
"""

import argparse
import json
import sys
from pathlib import Path

import requests

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from train_embedding_classifier import EmbeddingClassifier
from train_ak_classifier import AKClassifier

# Production API
API_BASE = "http://192.168.0.124:8000"


def fetch_all_items(limit: int = None) -> list[dict]:
    """Fetch all items from production."""
    items = []
    page = 1
    page_size = 100

    while True:
        url = f"{API_BASE}/api/items?page={page}&page_size={page_size}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        items.extend(data["items"])
        print(f"  Fetched page {page}: {len(data['items'])} items (total: {len(items)})")

        if limit and len(items) >= limit:
            items = items[:limit]
            break

        if len(data["items"]) < page_size:
            break
        page += 1

    return items


def classify_items(items: list[dict], relevance_clf, ak_clf) -> list[dict]:
    """Classify items using sklearn models."""
    results = []

    # Prepare texts
    texts = []
    for item in items:
        title = item.get("title", "")
        content = item.get("content", "")[:6000]
        source = item.get("source", {}).get("name", "")
        text = f"{title} {content}"
        if source:
            text += f" Quelle: {source}"
        texts.append(text)

    print(f"  Classifying {len(texts)} items...")

    # Batch relevance classification
    rel_predictions = relevance_clf.predict_batch(texts)

    # For relevant items, also get AK
    relevant_indices = [i for i, p in enumerate(rel_predictions) if p["relevant"]]
    relevant_texts = [texts[i] for i in relevant_indices]

    ak_predictions = {}
    if relevant_texts:
        ak_results = ak_clf.predict_batch(relevant_texts)
        for i, idx in enumerate(relevant_indices):
            ak_predictions[idx] = ak_results[i]

    # Build results
    for i, item in enumerate(items):
        rel_pred = rel_predictions[i]
        result = {
            "id": item["id"],
            "title": item["title"][:80],
            "sklearn_relevant": rel_pred["relevant"],
            "sklearn_relevance_conf": rel_pred.get("relevance_confidence", rel_pred.get("confidence", 0)),
        }

        # Get existing LLM analysis
        llm_analysis = item.get("metadata", {}).get("llm_analysis", {})
        result["llm_relevant"] = llm_analysis.get("relevance_score", 0) >= 0.5
        result["llm_relevance_score"] = llm_analysis.get("relevance_score", 0)
        result["llm_ak"] = llm_analysis.get("assigned_ak")
        result["llm_priority"] = llm_analysis.get("priority_suggestion") or item.get("priority")

        if i in ak_predictions:
            ak_pred = ak_predictions[i]
            result["sklearn_ak"] = ak_pred["ak"]
            result["sklearn_ak_conf"] = ak_pred["confidence"]
        else:
            result["sklearn_ak"] = None
            result["sklearn_ak_conf"] = None

        results.append(result)

    return results


def analyze_results(results: list[dict]) -> dict:
    """Analyze classification results."""
    stats = {
        "total": len(results),
        "relevance_agree": 0,
        "relevance_disagree": 0,
        "sklearn_relevant": 0,
        "llm_relevant": 0,
        "ak_agree": 0,
        "ak_disagree": 0,
        "sklearn_to_relevant": [],  # LLM said irrelevant, sklearn says relevant
        "sklearn_to_irrelevant": [],  # LLM said relevant, sklearn says irrelevant
        "ak_changes": [],
    }

    for r in results:
        # Relevance comparison
        if r["sklearn_relevant"] == r["llm_relevant"]:
            stats["relevance_agree"] += 1
        else:
            stats["relevance_disagree"] += 1
            if r["sklearn_relevant"] and not r["llm_relevant"]:
                stats["sklearn_to_relevant"].append(r)
            else:
                stats["sklearn_to_irrelevant"].append(r)

        if r["sklearn_relevant"]:
            stats["sklearn_relevant"] += 1
        if r["llm_relevant"]:
            stats["llm_relevant"] += 1

        # AK comparison (only for items both say are relevant)
        if r["sklearn_relevant"] and r["llm_relevant"] and r["sklearn_ak"] and r["llm_ak"]:
            if r["sklearn_ak"] == r["llm_ak"]:
                stats["ak_agree"] += 1
            else:
                stats["ak_disagree"] += 1
                stats["ak_changes"].append(r)

    return stats


def print_analysis(stats: dict):
    """Print analysis results."""
    print("\n" + "=" * 70)
    print("CLASSIFICATION COMPARISON: sklearn vs LLM")
    print("=" * 70)

    print(f"\nTotal items: {stats['total']}")

    print(f"\n--- RELEVANCE ---")
    print(f"Agreement: {stats['relevance_agree']} ({stats['relevance_agree']/stats['total']*100:.1f}%)")
    print(f"Disagreement: {stats['relevance_disagree']} ({stats['relevance_disagree']/stats['total']*100:.1f}%)")
    print(f"  sklearn says RELEVANT, LLM said irrelevant: {len(stats['sklearn_to_relevant'])}")
    print(f"  sklearn says IRRELEVANT, LLM said relevant: {len(stats['sklearn_to_irrelevant'])}")
    print(f"\nsklearn relevant: {stats['sklearn_relevant']} ({stats['sklearn_relevant']/stats['total']*100:.1f}%)")
    print(f"LLM relevant: {stats['llm_relevant']} ({stats['llm_relevant']/stats['total']*100:.1f}%)")

    if stats["ak_agree"] + stats["ak_disagree"] > 0:
        ak_total = stats["ak_agree"] + stats["ak_disagree"]
        print(f"\n--- AK ASSIGNMENT (both relevant) ---")
        print(f"Agreement: {stats['ak_agree']} ({stats['ak_agree']/ak_total*100:.1f}%)")
        print(f"Disagreement: {stats['ak_disagree']} ({stats['ak_disagree']/ak_total*100:.1f}%)")

    # Show examples of disagreements
    if stats["sklearn_to_relevant"][:5]:
        print("\n--- Examples: sklearn=RELEVANT, LLM=irrelevant ---")
        for r in stats["sklearn_to_relevant"][:5]:
            print(f"  [{r['sklearn_relevance_conf']:.0%}] {r['title']}")

    if stats["sklearn_to_irrelevant"][:5]:
        print("\n--- Examples: sklearn=IRRELEVANT, LLM=relevant ---")
        for r in stats["sklearn_to_irrelevant"][:5]:
            print(f"  [LLM:{r['llm_relevance_score']:.0%}] {r['title']}")

    if stats["ak_changes"][:5]:
        print("\n--- Examples: AK differences ---")
        for r in stats["ak_changes"][:5]:
            print(f"  sklearn={r['sklearn_ak']}, LLM={r['llm_ak']}: {r['title']}")


def main():
    parser = argparse.ArgumentParser(description="Reclassify production items")
    parser.add_argument("--apply", action="store_true", help="Apply changes via API")
    parser.add_argument("--limit", type=int, help="Limit number of items to process")
    parser.add_argument("--save", type=str, help="Save results to JSON file")
    args = parser.parse_args()

    print("=" * 70)
    print("Production Reclassification with sklearn")
    print("=" * 70)

    # Load classifiers
    print("\n[1/3] Loading classifiers...")
    relevance_clf = EmbeddingClassifier.load()
    print("  Relevance classifier loaded")
    ak_clf = AKClassifier.load()
    print("  AK classifier loaded")

    # Fetch items
    print("\n[2/3] Fetching items from production...")
    items = fetch_all_items(limit=args.limit)
    print(f"  Total items: {len(items)}")

    # Classify
    print("\n[3/3] Running sklearn classification...")
    results = classify_items(items, relevance_clf, ak_clf)

    # Analyze
    stats = analyze_results(results)
    print_analysis(stats)

    # Save results
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\nResults saved to: {args.save}")

    if args.apply:
        print("\n[!] --apply not implemented yet. Would update items via API.")
        # TODO: Implement API updates if desired


if __name__ == "__main__":
    main()
