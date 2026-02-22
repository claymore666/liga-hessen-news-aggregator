#!/usr/bin/env python3
"""Run the ML classifier against the fixed eval set and measure quality.

Content comes from the eval_set.json snapshot — no API needed. Uses the same
embedding + prediction pipeline as evaluate_model.py but against the fixed
eval set instead of the training split.

Usage:
    EMBEDDING_BACKEND=nomic-v2 python scripts/run_classifier_eval.py
    EMBEDDING_BACKEND=nomic-v2 python scripts/run_classifier_eval.py --model models/embedding/old.pkl --label baseline
    EMBEDDING_BACKEND=nomic-v2 python scripts/run_classifier_eval.py --label retrained-v2

IMPORTANT: Always set EMBEDDING_BACKEND=nomic-v2 (production embedder).

Requires:
    - evaluations/eval_set.json (run curate_eval_set.py first)
    - Classifier model .pkl file
"""

import argparse
import hashlib
import json
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

# Add parent directory to path for config/utils import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from config import MODELS_DIR, PRIORITY_LEVELS
from utils import get_embedder

# ============================================================================
# Configuration
# ============================================================================

DEFAULT_MODEL = MODELS_DIR / "embedding" / "embedding_classifier_nomic-v2.pkl"
EVAL_SET_PATH = Path(__file__).parent.parent / "evaluations" / "eval_set.json"
RESULTS_DIR = Path(__file__).parent.parent / "evaluations" / "results"


# ============================================================================
# Model loading (from evaluate_model.py)
# ============================================================================

def load_model(model_path: Path) -> dict:
    """Load a pickled classifier model."""
    with open(model_path, "rb") as f:
        return pickle.load(f)


def predict_batch(model: dict, embeddings: np.ndarray) -> list[dict]:
    """Run predictions using model components directly."""
    relevance_clf = model["relevance_clf"]
    priority_clf = model["priority_clf"]
    ak_clf = model["ak_clf"]
    priority_encoder = model["priority_encoder"]
    ak_encoder = model["ak_encoder"]

    relevance_preds = relevance_clf.predict(embeddings)
    relevance_probs = relevance_clf.predict_proba(embeddings)

    results = []
    for i in range(len(embeddings)):
        is_relevant = bool(relevance_preds[i])
        result = {
            "relevant": is_relevant,
            "relevance_confidence": float(max(relevance_probs[i])),
            "priority": None,
            "ak": None,
        }
        results.append(result)

    # Stage 2 & 3: Only for relevant items
    relevant_indices = [i for i, r in enumerate(results) if r["relevant"]]
    if relevant_indices:
        X_relevant = embeddings[relevant_indices]
        try:
            priority_probs = priority_clf.predict_proba(X_relevant)
            ak_probs = ak_clf.predict_proba(X_relevant)

            for j, i in enumerate(relevant_indices):
                priority_idx = np.argmax(priority_probs[j])
                results[i]["priority"] = priority_encoder.inverse_transform([priority_idx])[0]
                ak_idx = np.argmax(ak_probs[j])
                results[i]["ak"] = ak_encoder.inverse_transform([ak_idx])[0]
        except Exception as e:
            print(f"Warning: {e}")

    return results


# ============================================================================
# Metrics
# ============================================================================

PRIORITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def compute_metrics(items: list[dict], predictions: list[dict]) -> dict:
    """Compute evaluation metrics against ground truth."""
    total = len(items)
    if total == 0:
        return {}

    results_detail = []
    for item, pred in zip(items, predictions):
        gt = item["ground_truth"]
        expected_relevant = gt["relevant"]
        got_relevant = pred["relevant"]

        expected_priority = gt.get("priority") or "none"
        got_priority = pred["priority"] or "none"
        expected_aks = gt.get("aks", [])

        correct_rel = (expected_relevant == got_relevant)
        correct_pri = (expected_priority == got_priority) if expected_relevant and got_relevant else None

        results_detail.append({
            "id": item["id"],
            "title": item["title"][:60],
            "category": item["category"],
            "subcategory": item.get("subcategory", ""),
            "expected_relevant": expected_relevant,
            "expected_priority": expected_priority,
            "expected_aks": expected_aks,
            "got_relevant": got_relevant,
            "got_priority": got_priority,
            "got_ak": pred.get("ak"),
            "confidence": pred["relevance_confidence"],
            "correct_relevance": correct_rel,
            "correct_priority": correct_pri,
        })

    # Relevance
    correct_rel = sum(1 for r in results_detail if r["correct_relevance"])
    tp = sum(1 for r in results_detail if r["expected_relevant"] and r["got_relevant"])
    tn = sum(1 for r in results_detail if not r["expected_relevant"] and not r["got_relevant"])
    fp = sum(1 for r in results_detail if not r["expected_relevant"] and r["got_relevant"])
    fn = sum(1 for r in results_detail if r["expected_relevant"] and not r["got_relevant"])

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    # Priority (exact match)
    priority_items = [r for r in results_detail if r["expected_relevant"] and r["got_relevant"]]
    priority_correct = sum(1 for r in priority_items if r.get("correct_priority"))
    priority_acc = priority_correct / len(priority_items) if priority_items else 0

    # Priority within-1-level (high→medium OK, high→none bad)
    priority_within_one = 0.0
    if priority_items:
        within = sum(
            1 for r in priority_items
            if abs(PRIORITY_ORDER.get(r["expected_priority"], 1)
                   - PRIORITY_ORDER.get(r["got_priority"], 1)) <= 1
        )
        priority_within_one = within / len(priority_items)

    # AK accuracy (classifier predicts single AK; compare against ground truth list)
    ak_items = [r for r in results_detail
                if r["expected_relevant"] and r["got_relevant"]
                and r.get("expected_aks")]
    ak_correct = 0
    ak_partial = 0
    if ak_items:
        for r in ak_items:
            expected_set = set(r["expected_aks"])
            got_ak = r.get("got_ak")
            if got_ak and got_ak in expected_set:
                if len(expected_set) == 1:
                    ak_correct += 1  # exact match (single AK)
                else:
                    ak_partial += 1  # hit one of multiple expected AKs
    ak_exact = ak_correct / len(ak_items) if ak_items else 0
    ak_overlap = (ak_correct + ak_partial) / len(ak_items) if ak_items else 0

    # Per-category
    categories = {}
    for r in results_detail:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "correct": 0}
        categories[cat]["total"] += 1
        if r["correct_relevance"]:
            categories[cat]["correct"] += 1

    # FP/FN by subcategory
    fp_by_subcategory = {}
    fn_by_subcategory = {}
    for r in results_detail:
        subcat = r.get("subcategory", "unknown")
        if not r["expected_relevant"] and r["got_relevant"]:  # FP
            fp_by_subcategory[subcat] = fp_by_subcategory.get(subcat, 0) + 1
        elif r["expected_relevant"] and not r["got_relevant"]:  # FN
            fn_by_subcategory[subcat] = fn_by_subcategory.get(subcat, 0) + 1

    metrics = {
        "accuracy": round(correct_rel / total, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "priority_accuracy": round(priority_acc, 4),
        "priority_within_one": round(priority_within_one, 4),
        "ak_exact_accuracy": round(ak_exact, 4),
        "ak_overlap_accuracy": round(ak_overlap, 4),
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "fp_by_subcategory": fp_by_subcategory,
        "fn_by_subcategory": fn_by_subcategory,
        "total_items": total,
        "categories": categories,
    }

    return metrics, results_detail


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Run classifier eval against fixed eval set")
    parser.add_argument("--model", type=str, default=None,
                        help=f"Path to .pkl model (default: {DEFAULT_MODEL})")
    parser.add_argument("--label", type=str, default=None,
                        help="Label for this eval run (e.g. 'baseline', 'retrained-v2')")
    parser.add_argument("--eval-set", type=str, default=None,
                        help=f"Path to eval set JSON (default: {EVAL_SET_PATH})")
    args = parser.parse_args()

    model_path = Path(args.model) if args.model else DEFAULT_MODEL
    eval_path = Path(args.eval_set) if args.eval_set else EVAL_SET_PATH

    # Validate
    if not model_path.exists():
        print(f"ERROR: Model not found: {model_path}")
        sys.exit(1)

    if not eval_path.exists():
        print(f"ERROR: Eval set not found: {eval_path}")
        print("Run curate_eval_set.py first.")
        sys.exit(1)

    # Load eval set
    eval_data = json.loads(eval_path.read_text())
    items = eval_data["items"]
    eval_version = eval_data.get("version", 1)

    # Filter to items with ground truth
    items = [i for i in items if i.get("ground_truth")]
    if not items:
        print("ERROR: No items with ground truth in eval set")
        sys.exit(1)

    # Label
    label = args.label or model_path.stem

    # Model fingerprint
    with open(model_path, "rb") as f:
        fingerprint = hashlib.md5(f.read()).hexdigest()[:12]

    print("=" * 80)
    print("CLASSIFIER EVALUATION")
    print("=" * 80)
    print(f"  Model: {model_path}")
    print(f"  Fingerprint: {fingerprint}")
    print(f"  Label: {label}")
    print(f"  Eval set: {eval_path} (v{eval_version})")
    print(f"  Items: {len(items)} (with ground truth)")

    # Load model
    print("\n[1/3] Loading model...")
    model = load_model(model_path)
    print(f"  Backend: {model.get('backend', 'unknown')}")

    # Prepare texts (matching data_loading.py format)
    texts = []
    for item in items:
        title = item.get("title", "")
        content = item.get("content", "")
        source = item.get("source", "")
        text = f"{title} {content}"
        if source:
            text += f" Quelle: {source}"
        texts.append(text)

    # Compute embeddings
    print("\n[2/3] Computing embeddings...")
    embedder = get_embedder()
    print(f"  Embedder: {embedder}")

    start = time.perf_counter()
    embeddings = np.array(embedder.encode(texts, show_progress_bar=True))
    embed_time = time.perf_counter() - start
    embed_speed = len(texts) / embed_time
    print(f"  Embedded {len(texts)} items in {embed_time:.1f}s ({embed_speed:.0f} items/sec)")

    # Predict
    print("\n[3/3] Running predictions...")
    predictions = predict_batch(model, embeddings)

    # Compute metrics
    metrics, results_detail = compute_metrics(items, predictions)

    # Print results
    print(f"\n{'=' * 80}")
    print("RESULTS")
    print(f"{'=' * 80}")

    print(f"\n{'#':>3} {'ID':>6} {'Title':40.40} {'Expected':>8} {'Got':>8} {'Conf':>6} {'Match':>5}")
    print("-" * 90)

    for idx, r in enumerate(results_detail, 1):
        exp_str = r["expected_priority"] if r["expected_relevant"] else "none"
        got_str = r["got_priority"] if r["got_relevant"] else "none"
        mark = "OK" if r["correct_relevance"] else "MISS"
        print(f"{idx:>3} {r['id']:>6} {r['title']:40.40} {exp_str:>8} {got_str:>8} {r['confidence']:5.2f} {mark:>5}")

    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    print(f"  Accuracy:     {metrics['accuracy']:.1%}")
    print(f"  Precision:    {metrics['precision']:.1%}")
    print(f"  Recall:       {metrics['recall']:.1%}")
    print(f"  F1:           {metrics['f1']:.1%}")
    print(f"  Priority:     {metrics['priority_accuracy']:.1%} (exact)  {metrics['priority_within_one']:.1%} (within-1)")
    print(f"  AK:           {metrics['ak_exact_accuracy']:.1%} (exact)  {metrics['ak_overlap_accuracy']:.1%} (overlap)")
    print(f"  TP: {metrics['tp']}  TN: {metrics['tn']}  FP: {metrics['fp']}  FN: {metrics['fn']}")
    print(f"  Embed speed:  {embed_speed:.0f} items/sec")

    print(f"\n  By category:")
    for cat, vals in sorted(metrics["categories"].items()):
        pct = vals["correct"] / vals["total"] * 100 if vals["total"] > 0 else 0
        print(f"    {cat:15s}: {vals['correct']}/{vals['total']} ({pct:.0f}%)")

    # FP/FN by subcategory
    if metrics.get("fp_by_subcategory"):
        print(f"\n  False positives by type:")
        for subcat, count in sorted(metrics["fp_by_subcategory"].items(), key=lambda x: -x[1]):
            print(f"    {subcat:20s}: {count}")

    if metrics.get("fn_by_subcategory"):
        print(f"\n  False negatives by type:")
        for subcat, count in sorted(metrics["fn_by_subcategory"].items(), key=lambda x: -x[1]):
            print(f"    {subcat:20s}: {count}")

    # Show mismatches
    misses = [r for r in results_detail if not r["correct_relevance"]]
    if misses:
        print(f"\n  Mismatches ({len(misses)}):")
        for r in misses:
            exp = "REL" if r["expected_relevant"] else "IRR"
            got = "REL" if r["got_relevant"] else "IRR"
            print(f"    {r['id']:>6}: expected={exp}/{r['expected_priority']}, got={got}/{r['got_priority']} (conf={r['confidence']:.2f}) [{r.get('subcategory', '')}] — {r['title']}")

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    result_file = RESULTS_DIR / f"classifier_{date_str}_{label}.json"

    output = {
        "type": "classifier",
        "timestamp": datetime.now().isoformat(),
        "model_path": str(model_path),
        "model_fingerprint": fingerprint,
        "label": label,
        "eval_set_version": eval_version,
        "eval_set_path": str(eval_path),
        "embed_speed_items_per_sec": round(embed_speed, 1),
        "metrics": metrics,
        "results": results_detail,
    }

    result_file.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nResults saved: {result_file}")


if __name__ == "__main__":
    main()
