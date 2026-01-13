#!/usr/bin/env python3
"""
Compare Classifier Predictions vs LLM Ground Truth

This script fetches items from the production database (LLM-curated labels)
and compares them against classifier predictions.

Usage:
    # Compare single-label classifier
    python scripts/compare_classifier_vs_llm.py

    # Compare multi-label classifier
    python scripts/compare_classifier_vs_llm.py --multilabel

    # Limit to N items for quick test
    python scripts/compare_classifier_vs_llm.py --limit 100

    # Show detailed mismatches
    python scripts/compare_classifier_vs_llm.py --verbose
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.request import Request, urlopen

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import AK_CLASSES, MODELS_DIR, PRIORITY_LEVELS

API_URL = "http://localhost:8000/api"


def fetch_items(limit: int = 0) -> list[dict]:
    """Fetch items from production API."""
    items = []
    page = 1
    page_size = 100

    while True:
        url = f"{API_URL}/items?page={page}&page_size={page_size}&relevant_only=false"

        try:
            req = Request(url)
            with urlopen(req, timeout=30) as resp:
                data = json.load(resp)
                batch = data.get("items", [])
                total = data.get("total", 0)

                if not batch:
                    break

                items.extend(batch)

                if limit and len(items) >= limit:
                    items = items[:limit]
                    break

                if len(items) >= total:
                    break
                page += 1
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break

    return items


def load_classifier(multilabel: bool = False, backend: str = "nomic-v2"):
    """Load classifier model."""
    import pickle

    model_dir = MODELS_DIR / "embedding"

    if multilabel:
        filepath = model_dir / f"multilabel_classifier_{backend}.pkl"
    else:
        filepath = model_dir / f"embedding_classifier_{backend}.pkl"

    if not filepath.exists():
        raise FileNotFoundError(f"Model not found: {filepath}")

    # Need to add parent module to path for unpickling
    import sys
    parent_dir = str(Path(__file__).parent.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    # Import the class so pickle can find it
    if multilabel:
        from experiments.train_multilabel_classifier import MultilabelEmbeddingClassifier
    else:
        from train_embedding_classifier import EmbeddingClassifier

    with open(filepath, "rb") as f:
        return pickle.load(f)


def compare(items: list[dict], classifier, multilabel: bool = False, verbose: bool = False):
    """Compare classifier predictions against LLM ground truth."""

    # Prepare texts
    texts = []
    llm_labels = []

    for item in items:
        title = item.get("title", "")
        content = item.get("content", "")
        texts.append(f"{title} {content}")

        priority = item.get("priority", "none")
        is_relevant = priority in PRIORITY_LEVELS

        llm_labels.append({
            "relevant": is_relevant,
            "priority": priority if is_relevant else None,
            "ak": item.get("assigned_ak"),
            "aks": item.get("assigned_aks", []),
        })

    # Get predictions
    print("Running classifier predictions...")
    predictions = classifier.predict_batch(texts)

    # =========================================================================
    # Relevance Comparison
    # =========================================================================
    print("\n" + "=" * 60)
    print("RELEVANCE COMPARISON")
    print("=" * 60)

    rel_correct = 0
    rel_total = len(items)
    false_positives = []
    false_negatives = []

    for i, (llm, pred, item) in enumerate(zip(llm_labels, predictions, items)):
        llm_rel = llm["relevant"]
        pred_rel = pred["relevant"]

        if llm_rel == pred_rel:
            rel_correct += 1
        elif pred_rel and not llm_rel:
            false_positives.append((item, pred))
        else:
            false_negatives.append((item, pred))

    rel_acc = rel_correct / rel_total
    print(f"Accuracy: {rel_acc:.1%} ({rel_correct}/{rel_total})")
    print(f"False Positives (classifier says relevant, LLM says not): {len(false_positives)}")
    print(f"False Negatives (classifier says not relevant, LLM says relevant): {len(false_negatives)}")

    if verbose and false_negatives:
        print("\nFalse Negatives (missed relevant items):")
        for item, pred in false_negatives[:5]:
            print(f"  - {item['title'][:60]}...")
            print(f"    LLM: relevant, priority={item['priority']}, ak={item.get('assigned_ak')}")

    # =========================================================================
    # Priority Comparison (relevant items only)
    # =========================================================================
    print("\n" + "=" * 60)
    print("PRIORITY COMPARISON (relevant items)")
    print("=" * 60)

    pri_correct = 0
    pri_within_one = 0
    pri_total = 0

    priority_order = {p: i for i, p in enumerate(PRIORITY_LEVELS)}

    for llm, pred in zip(llm_labels, predictions):
        if not llm["relevant"] or llm["priority"] not in PRIORITY_LEVELS:
            continue

        pri_total += 1
        pred_priority = pred.get("priority")

        if pred_priority == llm["priority"]:
            pri_correct += 1
            pri_within_one += 1
        elif pred_priority in priority_order and llm["priority"] in priority_order:
            if abs(priority_order[pred_priority] - priority_order[llm["priority"]]) <= 1:
                pri_within_one += 1

    if pri_total > 0:
        print(f"Exact match: {pri_correct/pri_total:.1%} ({pri_correct}/{pri_total})")
        print(f"Within 1 level: {pri_within_one/pri_total:.1%} ({pri_within_one}/{pri_total})")

        # Priority confusion
        pri_confusion = Counter()
        for llm, pred in zip(llm_labels, predictions):
            if llm["relevant"] and llm["priority"] in PRIORITY_LEVELS:
                pri_confusion[(llm["priority"], pred.get("priority"))] += 1

        print("\nPriority confusion (LLM → Classifier):")
        for (llm_p, pred_p), count in pri_confusion.most_common(10):
            if llm_p != pred_p:
                print(f"  {llm_p} → {pred_p}: {count}")

    # =========================================================================
    # AK Comparison
    # =========================================================================
    print("\n" + "=" * 60)
    print("AK COMPARISON (relevant items)")
    print("=" * 60)

    ak_exact = 0
    ak_partial = 0
    ak_total = 0
    ak_mismatches = []

    for i, (llm, pred, item) in enumerate(zip(llm_labels, predictions, items)):
        if not llm["relevant"]:
            continue

        llm_aks = set(llm.get("aks") or ([llm["ak"]] if llm["ak"] else []))
        if not llm_aks:
            continue

        ak_total += 1

        if multilabel:
            pred_aks = set(pred.get("aks", []))
        else:
            pred_ak = pred.get("ak")
            pred_aks = {pred_ak} if pred_ak else set()

        # Exact match (all AKs correct)
        if llm_aks == pred_aks:
            ak_exact += 1
            ak_partial += 1
        # Partial match (at least one AK overlaps)
        elif llm_aks & pred_aks:
            ak_partial += 1
        else:
            ak_mismatches.append((item, llm_aks, pred_aks))

    if ak_total > 0:
        print(f"Exact match: {ak_exact/ak_total:.1%} ({ak_exact}/{ak_total})")
        print(f"Partial match (>=1 correct): {ak_partial/ak_total:.1%} ({ak_partial}/{ak_total})")

        # Per-AK accuracy
        print("\nPer-AK accuracy:")
        for ak in AK_CLASSES:
            ak_items = [(llm, pred) for llm, pred in zip(llm_labels, predictions)
                        if llm["relevant"] and ak in (llm.get("aks") or [llm.get("ak")])]
            if ak_items:
                if multilabel:
                    correct = sum(1 for llm, pred in ak_items if ak in pred.get("aks", []))
                else:
                    correct = sum(1 for llm, pred in ak_items if pred.get("ak") == ak)
                print(f"  {ak}: {correct}/{len(ak_items)} ({correct/len(ak_items):.1%})")

        if verbose and ak_mismatches:
            print("\nAK mismatches (no overlap):")
            for item, llm_aks, pred_aks in ak_mismatches[:10]:
                print(f"  - {item['title'][:50]}...")
                print(f"    LLM: {llm_aks}, Classifier: {pred_aks}")

    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Items evaluated:     {len(items)}")
    print(f"Relevance accuracy:  {rel_acc:.1%}")
    if pri_total > 0:
        print(f"Priority accuracy:   {pri_correct/pri_total:.1%} (exact), {pri_within_one/pri_total:.1%} (within 1)")
    if ak_total > 0:
        print(f"AK accuracy:         {ak_exact/ak_total:.1%} (exact), {ak_partial/ak_total:.1%} (partial)")
    print(f"Classifier type:     {'multi-label' if multilabel else 'single-label'}")

    return {
        "relevance_accuracy": rel_acc,
        "priority_accuracy": pri_correct / pri_total if pri_total > 0 else 0,
        "ak_exact_accuracy": ak_exact / ak_total if ak_total > 0 else 0,
        "ak_partial_accuracy": ak_partial / ak_total if ak_total > 0 else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Compare classifier vs LLM predictions")
    parser.add_argument("--multilabel", action="store_true", help="Use multi-label classifier")
    parser.add_argument("--backend", default="nomic-v2", help="Embedding backend")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of items")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed mismatches")
    args = parser.parse_args()

    print("=" * 60)
    print("Classifier vs LLM Comparison")
    print("=" * 60)
    print(f"Classifier: {'multi-label' if args.multilabel else 'single-label'}")
    print(f"Backend: {args.backend}")

    # Fetch items
    print(f"\nFetching items from production API{f' (limit {args.limit})' if args.limit else ''}...")
    items = fetch_items(limit=args.limit)
    print(f"Fetched {len(items)} items")

    # Load classifier
    print(f"\nLoading classifier...")
    try:
        clf = load_classifier(multilabel=args.multilabel, backend=args.backend)
        print(f"Loaded: {type(clf).__name__}")
    except FileNotFoundError as e:
        print(f"ERROR: Classifier not found: {e}")
        print("\nTo train the classifier first, run:")
        if args.multilabel:
            print(f"  EMBEDDING_BACKEND={args.backend} python experiments/train_multilabel_classifier.py")
        else:
            print(f"  EMBEDDING_BACKEND={args.backend} python train_embedding_classifier.py")
        sys.exit(1)

    # Compare
    compare(items, clf, multilabel=args.multilabel, verbose=args.verbose)


if __name__ == "__main__":
    main()
