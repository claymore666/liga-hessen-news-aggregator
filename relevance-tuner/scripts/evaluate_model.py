#!/usr/bin/env python3
"""Evaluate an existing classifier model against the current test set.

Use this to establish a baseline before retraining, or to compare models.

Usage:
    # Evaluate the current deployed model (baseline before retraining)
    EMBEDDING_BACKEND=nomic-v2 python scripts/evaluate_model.py \
      --model models/embedding/embedding_classifier_nomic-v2.pkl.old \
      --label baseline

    # Evaluate the newly trained model
    EMBEDDING_BACKEND=nomic-v2 python scripts/evaluate_model.py \
      --model models/embedding/embedding_classifier_nomic-v2.pkl \
      --label retrained-2026-02-21

    # Compare all evaluations
    EMBEDDING_BACKEND=nomic-v2 python scripts/evaluate_model.py --history
"""

import argparse
import hashlib
import json
import os
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sklearn.metrics import accuracy_score, classification_report, f1_score

from config import AK_CLASSES, MODELS_DIR, PRIORITY_LEVELS
from utils import get_embedder, load_test_data


EVAL_HISTORY_FILE = MODELS_DIR / "embedding" / "eval_history.json"


def load_model(model_path: Path):
    """Load a pickled classifier model and wrap it for prediction."""
    with open(model_path, "rb") as f:
        data = pickle.load(f)

    # The model is stored as a dict with individual classifiers
    return data


def predict_batch(model: dict, embeddings: np.ndarray) -> list[dict]:
    """Run predictions using model components directly."""
    relevance_clf = model["relevance_clf"]
    priority_clf = model["priority_clf"]
    ak_clf = model["ak_clf"]
    priority_encoder = model["priority_encoder"]
    ak_encoder = model["ak_encoder"]

    # Stage 1: Relevance
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


def evaluate(predictions: list[dict], relevance: list[int], priorities: list[str], aks: list[str]) -> dict:
    """Evaluate predictions against ground truth."""
    # Relevance
    y_true_rel = np.array(relevance)
    y_pred_rel = np.array([1 if p["relevant"] else 0 for p in predictions])

    rel_acc = accuracy_score(y_true_rel, y_pred_rel)
    rel_f1 = f1_score(y_true_rel, y_pred_rel)

    print("\n=== RELEVANCE (binary) ===")
    print(classification_report(y_true_rel, y_pred_rel, target_names=["irrelevant", "relevant"]))
    print(f"Accuracy: {rel_acc:.1%}, F1: {rel_f1:.1%}")

    # Priority (on true relevant)
    relevant_mask = y_true_rel == 1
    priorities_arr = np.array(priorities)
    valid_priority = np.array([p in PRIORITY_LEVELS for p in priorities_arr])
    eval_mask = relevant_mask & valid_priority

    priority_acc = 0.0
    within_one = 0.0
    if np.sum(eval_mask) > 0:
        y_true_priority = priorities_arr[eval_mask]
        y_pred_priority = np.array([p["priority"] or "medium" for p in predictions])[eval_mask]

        priority_acc = accuracy_score(y_true_priority, y_pred_priority)
        print("\n=== PRIORITY (3-class) ===")
        print(classification_report(y_true_priority, y_pred_priority, labels=PRIORITY_LEVELS, zero_division=0))
        print(f"Accuracy: {priority_acc:.1%}")

        level_map = {p: i for i, p in enumerate(PRIORITY_LEVELS)}
        within_one = float(np.mean([
            abs(level_map[t] - level_map.get(p, 1)) <= 1
            for t, p in zip(y_true_priority, y_pred_priority)
        ]))
        print(f"Within-1-level: {within_one:.1%}")

    # AK (on true relevant)
    aks_arr = np.array(aks)
    valid_ak = np.array([a in AK_CLASSES for a in aks_arr])
    eval_mask = relevant_mask & valid_ak

    ak_acc = 0.0
    if np.sum(eval_mask) > 0:
        y_true_ak = aks_arr[eval_mask]
        y_pred_ak = np.array([p["ak"] or "QAG" for p in predictions])[eval_mask]

        ak_acc = accuracy_score(y_true_ak, y_pred_ak)
        print("\n=== AK (6-class) ===")
        print(classification_report(y_true_ak, y_pred_ak, labels=AK_CLASSES, zero_division=0))
        print(f"Accuracy: {ak_acc:.1%}")

    return {
        "relevance_accuracy": round(rel_acc, 4),
        "relevance_f1": round(rel_f1, 4),
        "priority_accuracy": round(priority_acc, 4),
        "priority_within_one": round(within_one, 4),
        "ak_accuracy": round(ak_acc, 4),
    }


def save_eval_result(label: str, metrics: dict, model_path: str, test_size: int, embed_speed: float):
    """Append evaluation result to history file."""
    history = []
    if EVAL_HISTORY_FILE.exists():
        with open(EVAL_HISTORY_FILE) as f:
            history = json.load(f)

    # Compute model fingerprint
    with open(model_path, "rb") as f:
        fingerprint = hashlib.md5(f.read()).hexdigest()[:12]

    entry = {
        "label": label,
        "model_path": str(model_path),
        "model_fingerprint": fingerprint,
        "test_size": test_size,
        "embed_speed_items_per_sec": round(embed_speed, 1),
        "timestamp": datetime.now().isoformat(),
        **metrics,
    }

    history.append(entry)

    EVAL_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVAL_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

    print(f"\nSaved to {EVAL_HISTORY_FILE}")
    return entry


def print_history():
    """Print evaluation history as comparison table."""
    if not EVAL_HISTORY_FILE.exists():
        print("No evaluation history found.")
        return

    with open(EVAL_HISTORY_FILE) as f:
        history = json.load(f)

    if not history:
        print("Evaluation history is empty.")
        return

    print("=" * 90)
    print("CLASSIFIER EVALUATION HISTORY")
    print("=" * 90)
    print(f"{'Label':<25} {'Date':<12} {'Relevance':>10} {'Rel F1':>8} {'Priority':>10} {'AK':>8} {'Test':>6} {'Speed':>8}")
    print("-" * 90)

    for entry in history:
        date = entry["timestamp"][:10]
        print(
            f"{entry['label']:<25} "
            f"{date:<12} "
            f"{entry['relevance_accuracy']:>9.1%} "
            f"{entry['relevance_f1']:>7.1%} "
            f"{entry['priority_accuracy']:>9.1%} "
            f"{entry['ak_accuracy']:>7.1%} "
            f"{entry['test_size']:>6} "
            f"{entry.get('embed_speed_items_per_sec', 0):>6.0f}/s"
        )

    # Show delta between last two entries
    if len(history) >= 2:
        prev = history[-2]
        curr = history[-1]
        print("-" * 90)

        def delta(key):
            d = curr[key] - prev[key]
            return f"+{d:.1%}" if d >= 0 else f"{d:.1%}"

        print(
            f"{'Delta (last two)':<25} "
            f"{'':12} "
            f"{delta('relevance_accuracy'):>10} "
            f"{delta('relevance_f1'):>8} "
            f"{delta('priority_accuracy'):>10} "
            f"{delta('ak_accuracy'):>8}"
        )

    print("=" * 90)


def main():
    parser = argparse.ArgumentParser(description="Evaluate a classifier model against the test set")
    parser.add_argument("--model", type=str, help="Path to the .pkl model file to evaluate")
    parser.add_argument("--label", type=str, default=None, help="Label for this evaluation run (e.g. 'baseline', 'retrained-2026-02-21')")
    parser.add_argument("--history", action="store_true", help="Print evaluation history and exit")
    args = parser.parse_args()

    if args.history:
        print_history()
        return

    if not args.model:
        parser.error("--model is required (unless using --history)")

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"Error: Model file not found: {model_path}")
        sys.exit(1)

    label = args.label or model_path.stem

    print("=" * 60)
    print(f"Evaluate Classifier: {label}")
    print("=" * 60)
    print(f"  Model: {model_path}")

    # Load model
    print("\n[1/4] Loading model...")
    model = load_model(model_path)
    print(f"  Backend: {model.get('backend', 'unknown')}")

    # Load test data
    print("\n[2/4] Loading test data...")
    test_texts, test_rel, test_pri, test_ak = load_test_data()
    print(f"  Test items: {len(test_texts)}")
    print(f"  Relevant: {sum(test_rel)} ({sum(test_rel)/len(test_rel)*100:.1f}%)")

    # Compute embeddings
    print("\n[3/4] Computing embeddings...")
    embedder = get_embedder()
    print(f"  Embedder: {embedder}")

    start = time.perf_counter()
    embeddings = np.array(embedder.encode(test_texts, show_progress_bar=True))
    embed_time = time.perf_counter() - start
    embed_speed = len(test_texts) / embed_time
    print(f"  Embedded {len(test_texts)} items in {embed_time:.1f}s ({embed_speed:.0f} items/sec)")

    # Predict and evaluate
    print("\n[4/4] Evaluating...")
    predictions = predict_batch(model, embeddings)
    metrics = evaluate(predictions, test_rel, test_pri, test_ak)

    # Save result
    entry = save_eval_result(label, metrics, model_path, len(test_texts), embed_speed)

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Label:              {label}")
    print(f"Model fingerprint:  {entry['model_fingerprint']}")
    print(f"Relevance accuracy: {metrics['relevance_accuracy']:.1%}")
    print(f"Relevance F1:       {metrics['relevance_f1']:.1%}")
    print(f"Priority accuracy:  {metrics['priority_accuracy']:.1%}")
    print(f"Priority within-1:  {metrics['priority_within_one']:.1%}")
    print(f"AK accuracy:        {metrics['ak_accuracy']:.1%}")

    # Show history if we have more than one entry
    print()
    print_history()


if __name__ == "__main__":
    main()
