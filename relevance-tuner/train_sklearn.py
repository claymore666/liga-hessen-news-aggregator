#!/usr/bin/env python3
"""
Liga Hessen Relevance Classifier - Scikit-learn Approach (Knowledge Distillation)

Uses Qwen3-labeled data to train fast scikit-learn classifiers.
Approach: Teacher (Qwen3) → Student (Scikit-learn)

Speed comparison:
- Qwen3 fine-tuned: ~1.3s per item
- Scikit-learn: ~0.001s per item (1000x faster!)
"""

import json
import pickle
import time
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, accuracy_score
from sklearn.pipeline import Pipeline

# ============================================================================
# Configuration
# ============================================================================

DATA_DIR = Path(__file__).parent / "data" / "final"
MODEL_DIR = Path(__file__).parent / "models" / "sklearn"

# ============================================================================
# Data Loading
# ============================================================================

def load_jsonl(path: Path) -> list[dict]:
    """Load JSONL file."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def prepare_features(records: list[dict]) -> tuple[list[str], dict]:
    """Extract text features and labels from records."""
    texts = []
    labels = {
        "relevant": [],
        "ak": [],
        "priority": [],
    }

    for r in records:
        inp = r["input"]
        lab = r["labels"]

        # Combine title and content for classification
        text = f"{inp['title']} {inp['content'][:1500]} Quelle: {inp['source']}"
        texts.append(text)

        # Labels
        labels["relevant"].append(1 if lab["relevant"] else 0)
        labels["ak"].append(lab.get("ak") or "null")
        labels["priority"].append(lab.get("priority") or "null")

    return texts, labels


# ============================================================================
# Model Training
# ============================================================================

class LigaClassifier:
    """Fast classifier ensemble for Liga relevance classification."""

    def __init__(self):
        # Shared vectorizer
        self.vectorizer = TfidfVectorizer(
            max_features=10000,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.95,
            sublinear_tf=True,
        )

        # Task-specific classifiers
        self.relevance_clf = LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            random_state=42,
        )

        self.ak_clf = LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            random_state=42,
        )

        self.priority_clf = LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            random_state=42,
        )

        self.is_fitted = False

    def fit(self, texts: list[str], labels: dict):
        """Train all classifiers."""
        print("  Fitting TF-IDF vectorizer...")
        X = self.vectorizer.fit_transform(texts)
        print(f"  Feature matrix: {X.shape}")

        # Task 1: Relevance (binary)
        print("  Training relevance classifier...")
        self.relevance_clf.fit(X, labels["relevant"])

        # Task 2: AK classification (only on relevant items)
        relevant_mask = np.array(labels["relevant"]) == 1
        X_relevant = X[relevant_mask]

        print("  Training AK classifier...")
        ak_labels = np.array(labels["ak"])[relevant_mask]
        self.ak_clf.fit(X_relevant, ak_labels)

        # Task 3: Priority classification (only on relevant items)
        print("  Training priority classifier...")
        priority_labels = np.array(labels["priority"])[relevant_mask]
        self.priority_clf.fit(X_relevant, priority_labels)

        self.is_fitted = True
        print("  Training complete!")

    def predict(self, text: str) -> dict:
        """Predict relevance, AK, and priority for a single text."""
        X = self.vectorizer.transform([text])

        # Relevance
        relevant_prob = self.relevance_clf.predict_proba(X)[0]
        is_relevant = self.relevance_clf.predict(X)[0]

        result = {
            "relevant": bool(is_relevant),
            "relevant_confidence": float(max(relevant_prob)),
            "ak": None,
            "ak_confidence": None,
            "priority": None,
            "priority_confidence": None,
        }

        if is_relevant:
            # AK
            ak_prob = self.ak_clf.predict_proba(X)[0]
            result["ak"] = self.ak_clf.predict(X)[0]
            result["ak_confidence"] = float(max(ak_prob))

            # Priority
            priority_prob = self.priority_clf.predict_proba(X)[0]
            result["priority"] = self.priority_clf.predict(X)[0]
            result["priority_confidence"] = float(max(priority_prob))

        return result

    def predict_batch(self, texts: list[str]) -> list[dict]:
        """Predict for multiple texts (faster than individual calls)."""
        X = self.vectorizer.transform(texts)

        # Batch predictions
        relevant_preds = self.relevance_clf.predict(X)
        relevant_probs = self.relevance_clf.predict_proba(X)

        ak_preds = self.ak_clf.predict(X)
        ak_probs = self.ak_clf.predict_proba(X)

        priority_preds = self.priority_clf.predict(X)
        priority_probs = self.priority_clf.predict_proba(X)

        results = []
        for i in range(len(texts)):
            is_relevant = bool(relevant_preds[i])
            result = {
                "relevant": is_relevant,
                "relevant_confidence": float(max(relevant_probs[i])),
                "ak": ak_preds[i] if is_relevant else None,
                "ak_confidence": float(max(ak_probs[i])) if is_relevant else None,
                "priority": priority_preds[i] if is_relevant else None,
                "priority_confidence": float(max(priority_probs[i])) if is_relevant else None,
            }
            results.append(result)

        return results

    def save(self, path: Path):
        """Save model to disk."""
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "classifier.pkl", "wb") as f:
            pickle.dump(self, f)
        print(f"  Model saved to: {path / 'classifier.pkl'}")

    @classmethod
    def load(cls, path: Path) -> "LigaClassifier":
        """Load model from disk."""
        with open(path / "classifier.pkl", "rb") as f:
            return pickle.load(f)


# ============================================================================
# Evaluation
# ============================================================================

def evaluate(clf: LigaClassifier, texts: list[str], labels: dict) -> dict:
    """Evaluate classifier on test set."""
    predictions = clf.predict_batch(texts)

    # Relevance
    y_true_rel = labels["relevant"]
    y_pred_rel = [1 if p["relevant"] else 0 for p in predictions]

    print("\n=== Relevance Classification ===")
    print(classification_report(y_true_rel, y_pred_rel, target_names=["irrelevant", "relevant"]))
    rel_acc = accuracy_score(y_true_rel, y_pred_rel)

    # AK (only for truly relevant items - compare against ground truth)
    relevant_mask = np.array(labels["relevant"]) == 1
    y_true_ak = np.array(labels["ak"])[relevant_mask]
    # Get AK predictions for items we should predict as relevant
    # Force prediction by calling ak_clf directly for these items
    X_relevant = clf.vectorizer.transform([texts[i] for i, m in enumerate(relevant_mask) if m])
    y_pred_ak = clf.ak_clf.predict(X_relevant)

    print("\n=== AK Classification (on truly relevant items) ===")
    print(classification_report(y_true_ak, y_pred_ak, zero_division=0))
    ak_acc = accuracy_score(y_true_ak, y_pred_ak)

    # Priority (only for truly relevant items)
    y_true_priority = np.array(labels["priority"])[relevant_mask]
    y_pred_priority = clf.priority_clf.predict(X_relevant)

    print("\n=== Priority Classification (on truly relevant items) ===")
    print(classification_report(y_true_priority, y_pred_priority, zero_division=0))
    priority_acc = accuracy_score(y_true_priority, y_pred_priority)

    return {
        "relevance_accuracy": rel_acc,
        "ak_accuracy": ak_acc,
        "priority_accuracy": priority_acc,
    }


def benchmark_speed(clf: LigaClassifier, texts: list[str], n_runs: int = 3) -> float:
    """Benchmark prediction speed."""
    times = []
    for _ in range(n_runs):
        start = time.perf_counter()
        clf.predict_batch(texts)
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    avg_time = np.mean(times)
    items_per_sec = len(texts) / avg_time
    return items_per_sec


# ============================================================================
# Main
# ============================================================================

def main():
    print("=" * 60)
    print("Liga Relevance Classifier - Scikit-learn Training")
    print("(Knowledge Distillation from Qwen3)")
    print("=" * 60)

    # Load data
    print("\n[1/4] Loading data...")
    train_data = load_jsonl(DATA_DIR / "train.jsonl")
    val_data = load_jsonl(DATA_DIR / "validation.jsonl")
    test_data = load_jsonl(DATA_DIR / "test.jsonl")

    print(f"  Train: {len(train_data)}")
    print(f"  Validation: {len(val_data)}")
    print(f"  Test: {len(test_data)}")

    # Prepare features
    print("\n[2/4] Preparing features...")
    train_texts, train_labels = prepare_features(train_data)
    val_texts, val_labels = prepare_features(val_data)
    test_texts, test_labels = prepare_features(test_data)

    # Combine train + val for final training
    all_train_texts = train_texts + val_texts
    all_train_labels = {
        k: train_labels[k] + val_labels[k]
        for k in train_labels
    }
    print(f"  Total training examples: {len(all_train_texts)}")

    # Train
    print("\n[3/4] Training classifiers...")
    clf = LigaClassifier()
    clf.fit(all_train_texts, all_train_labels)

    # Evaluate
    print("\n[4/4] Evaluating on test set...")
    metrics = evaluate(clf, test_texts, test_labels)

    # Speed benchmark
    print("\n=== Speed Benchmark ===")
    items_per_sec = benchmark_speed(clf, test_texts)
    print(f"  Speed: {items_per_sec:.0f} items/sec ({1000/items_per_sec:.3f}ms per item)")
    print(f"  Comparison: Qwen3 fine-tuned = ~0.77 items/sec (1300ms per item)")
    print(f"  Speedup: {items_per_sec / 0.77:.0f}x faster!")

    # Save model
    print("\n=== Saving Model ===")
    clf.save(MODEL_DIR)

    # Summary
    print("\n" + "=" * 60)
    print("Training Complete!")
    print("=" * 60)
    print(f"\nAccuracy:")
    print(f"  Relevance: {metrics['relevance_accuracy']:.1%}")
    print(f"  AK:        {metrics['ak_accuracy']:.1%}")
    print(f"  Priority:  {metrics['priority_accuracy']:.1%}")
    print(f"\nSpeed: {items_per_sec:.0f} items/sec")
    print(f"Model saved to: {MODEL_DIR / 'classifier.pkl'}")

    # Show example prediction
    print("\n=== Example Prediction ===")
    test_text = "Hessen kürzt Mittel für Kitas um 50 Millionen Euro. Die Landesregierung plant drastische Kürzungen. Quelle: hessenschau.de"
    pred = clf.predict(test_text)
    print(f"Input: {test_text[:80]}...")
    print(f"Output: relevant={pred['relevant']} ({pred['relevant_confidence']:.0%}), "
          f"ak={pred['ak']} ({pred['ak_confidence']:.0%}), "
          f"priority={pred['priority']} ({pred['priority_confidence']:.0%})")

    print("=" * 60)


if __name__ == "__main__":
    main()
