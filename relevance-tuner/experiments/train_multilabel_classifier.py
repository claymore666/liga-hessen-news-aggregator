#!/usr/bin/env python3
"""
Multi-Label Embedding Classifier Experiment

This experimental classifier supports multiple AK assignments per item,
unlike the production single-label classifier.

Key differences from train_embedding_classifier.py:
- AK prediction uses MultiOutputClassifier (binary per AK)
- Training data uses 'aks' field (list) instead of 'ak' (string)
- Evaluation includes multi-label metrics (subset accuracy, hamming loss)

Usage:
    # Train experimental multi-label classifier
    EMBEDDING_BACKEND=nomic-v2 python experiments/train_multilabel_classifier.py

    # Compare against LLM ground truth
    python experiments/train_multilabel_classifier.py --compare

Output:
    models/embedding/multilabel_classifier_nomic-v2.pkl
"""

import json
import os
import pickle
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    f1_score,
    hamming_loss,
)
from sklearn.multioutput import MultiOutputClassifier
from sklearn.preprocessing import LabelEncoder

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    AK_CLASSES,
    DATA_DIR,
    MODELS_DIR,
    PRIORITY_LEVELS,
    RANDOM_SEED,
    get_backend_config,
)
from utils import get_embedder

# ============================================================================
# Configuration
# ============================================================================

MODEL_DIR = MODELS_DIR / "embedding"


# ============================================================================
# Multi-Label Utilities
# ============================================================================


def create_multilabel_matrix(aks_list: list[list[str]], classes: list[str]) -> np.ndarray:
    """
    Convert list of AK lists to binary matrix.

    Args:
        aks_list: List of AK assignments, e.g., [["AK1", "AK3"], ["AK2"], ...]
        classes: Ordered list of all possible classes

    Returns:
        Binary matrix of shape (n_samples, n_classes)
    """
    class_to_idx = {c: i for i, c in enumerate(classes)}
    n_samples = len(aks_list)
    n_classes = len(classes)

    matrix = np.zeros((n_samples, n_classes), dtype=int)

    for i, aks in enumerate(aks_list):
        for ak in aks:
            if ak in class_to_idx:
                matrix[i, class_to_idx[ak]] = 1

    return matrix


def matrix_to_aks(matrix: np.ndarray, classes: list[str]) -> list[list[str]]:
    """Convert binary matrix back to list of AK lists."""
    result = []
    for row in matrix:
        aks = [classes[i] for i, v in enumerate(row) if v == 1]
        result.append(aks)
    return result


# ============================================================================
# Multi-Label Classifier
# ============================================================================


class MultilabelEmbeddingClassifier:
    """
    Embedding classifier with multi-label AK support.

    Stage 1: Relevance (binary) - same as single-label
    Stage 2: Priority (multi-class) - same as single-label
    Stage 3: AK (multi-label) - NEW: predicts multiple AKs per item
    """

    def __init__(self, backend_config: Optional[dict] = None):
        self.embedder = None
        self.backend_config = backend_config or {}

        # Classifier settings
        lr_c = self.backend_config.get("lr_c", 1.0)
        lr_max_iter = self.backend_config.get("lr_max_iter", 1000)
        rf_n_estimators = self.backend_config.get("rf_n_estimators", 200)
        rf_max_depth = self.backend_config.get("rf_max_depth", 15)

        # Stage 1: Relevance (binary)
        self.relevance_clf = LogisticRegression(
            max_iter=lr_max_iter,
            class_weight="balanced",
            C=lr_c,
            random_state=RANDOM_SEED,
        )

        # Stage 2: Priority (multi-class)
        self.priority_clf = RandomForestClassifier(
            n_estimators=rf_n_estimators,
            max_depth=rf_max_depth,
            class_weight="balanced",
            random_state=RANDOM_SEED,
            n_jobs=-1,
        )
        self.priority_encoder = LabelEncoder()

        # Stage 3: AK (multi-label) - KEY DIFFERENCE
        self.ak_clf = MultiOutputClassifier(
            RandomForestClassifier(
                n_estimators=rf_n_estimators,
                max_depth=rf_max_depth,
                class_weight="balanced",
                random_state=RANDOM_SEED,
                n_jobs=-1,
            )
        )
        self.ak_classes = AK_CLASSES  # Store for prediction

        self.is_fitted = False

    def _load_embedder(self):
        if self.embedder is None:
            print("  Loading embedder...")
            self.embedder = get_embedder()
            print(f"  Backend: {self.embedder}")
        return self.embedder

    def _embed(self, texts: list[str], show_progress: bool = True) -> np.ndarray:
        embedder = self._load_embedder()
        embeddings = embedder.encode(texts, show_progress_bar=show_progress)
        return np.array(embeddings)

    def fit(
        self,
        texts: list[str],
        relevance: list[int],
        priorities: list[str],
        aks_list: list[list[str]],  # Multi-label: list of AK lists
    ):
        """Train all classifiers."""
        print("  Computing embeddings...")
        X = self._embed(texts, show_progress=True)
        print(f"  Embedding matrix: {X.shape}")

        # Stage 1: Relevance
        print("  Training relevance classifier...")
        y_rel = np.array(relevance)
        self.relevance_clf.fit(X, y_rel)

        # Stage 2: Priority (only on relevant)
        print("  Training priority classifier...")
        relevant_mask = y_rel == 1
        priorities_arr = np.array(priorities)
        valid_priority = np.array([p in PRIORITY_LEVELS for p in priorities_arr])
        valid_mask = relevant_mask & valid_priority

        if np.sum(valid_mask) > 10:
            X_priority = X[valid_mask]
            y_priority = priorities_arr[valid_mask]
            self.priority_encoder.fit(PRIORITY_LEVELS)
            y_priority_enc = self.priority_encoder.transform(y_priority)
            self.priority_clf.fit(X_priority, y_priority_enc)

        # Stage 3: AK (multi-label, only on relevant)
        print("  Training multi-label AK classifier...")
        # Filter to relevant items with at least one valid AK
        valid_ak_mask = np.array([
            any(ak in AK_CLASSES for ak in aks) for aks in aks_list
        ])
        valid_mask = relevant_mask & valid_ak_mask

        if np.sum(valid_mask) > 10:
            X_ak = X[valid_mask]
            aks_filtered = [aks_list[i] for i in range(len(aks_list)) if valid_mask[i]]

            # Convert to binary matrix
            y_ak = create_multilabel_matrix(aks_filtered, AK_CLASSES)
            print(f"    Multi-label matrix shape: {y_ak.shape}")
            print(f"    Labels per item: min={y_ak.sum(axis=1).min()}, "
                  f"max={y_ak.sum(axis=1).max()}, "
                  f"mean={y_ak.sum(axis=1).mean():.2f}")

            self.ak_clf.fit(X_ak, y_ak)

        self.is_fitted = True
        print("  Training complete!")

    def predict(self, title: str, content: str, source: Optional[str] = None) -> dict:
        """Predict for a single item."""
        text = f"{title} {content}"
        if source:
            text += f" Quelle: {source}"

        X = self._embed([text], show_progress=False)

        # Stage 1: Relevance
        relevance_prob = self.relevance_clf.predict_proba(X)[0]
        is_relevant = self.relevance_clf.predict(X)[0]

        result = {
            "relevant": bool(is_relevant),
            "relevance_confidence": float(max(relevance_prob)),
            "priority": None,
            "priority_confidence": None,
            "aks": [],  # Multi-label output
            "ak_confidences": {},
        }

        if is_relevant:
            # Stage 2: Priority
            try:
                priority_prob = self.priority_clf.predict_proba(X)[0]
                priority_idx = np.argmax(priority_prob)
                result["priority"] = self.priority_encoder.inverse_transform([priority_idx])[0]
                result["priority_confidence"] = float(priority_prob[priority_idx])
            except Exception:
                result["priority"] = "medium"
                result["priority_confidence"] = 0.5

            # Stage 3: AK (multi-label)
            try:
                # Get probabilities for each AK
                ak_probs = []
                for estimator in self.ak_clf.estimators_:
                    prob = estimator.predict_proba(X)[0]
                    # prob[1] is probability of positive class
                    ak_probs.append(prob[1] if len(prob) > 1 else prob[0])

                # Predict binary labels
                ak_pred = self.ak_clf.predict(X)[0]

                # Collect predicted AKs with confidences
                predicted_aks = []
                confidences = {}
                for i, (ak, pred, prob) in enumerate(zip(self.ak_classes, ak_pred, ak_probs)):
                    if pred == 1:
                        predicted_aks.append(ak)
                    confidences[ak] = float(prob)

                result["aks"] = predicted_aks
                result["ak_confidences"] = confidences

                # Fallback if no AK predicted
                if not predicted_aks:
                    best_idx = np.argmax(ak_probs)
                    result["aks"] = [self.ak_classes[best_idx]]

            except Exception as e:
                print(f"Warning: AK prediction failed: {e}")
                result["aks"] = ["QAG"]
                result["ak_confidences"] = {"QAG": 0.5}

        return result

    def predict_batch(self, texts: list[str]) -> list[dict]:
        """Predict for multiple texts."""
        X = self._embed(texts, show_progress=False)

        # Stage 1: Relevance
        relevance_preds = self.relevance_clf.predict(X)
        relevance_probs = self.relevance_clf.predict_proba(X)

        results = []
        for i in range(len(texts)):
            is_relevant = bool(relevance_preds[i])
            result = {
                "relevant": is_relevant,
                "relevance_confidence": float(max(relevance_probs[i])),
                "priority": None,
                "priority_confidence": None,
                "aks": [],
                "ak_confidences": {},
            }
            results.append(result)

        # Stage 2 & 3: Only for relevant items
        relevant_indices = [i for i, r in enumerate(results) if r["relevant"]]
        if relevant_indices:
            X_relevant = X[relevant_indices]

            try:
                # Priority
                priority_probs = self.priority_clf.predict_proba(X_relevant)
                for j, i in enumerate(relevant_indices):
                    priority_idx = np.argmax(priority_probs[j])
                    results[i]["priority"] = self.priority_encoder.inverse_transform([priority_idx])[0]
                    results[i]["priority_confidence"] = float(priority_probs[j, priority_idx])

                # AK (multi-label)
                ak_preds = self.ak_clf.predict(X_relevant)
                for j, i in enumerate(relevant_indices):
                    predicted_aks = [
                        self.ak_classes[k]
                        for k, v in enumerate(ak_preds[j])
                        if v == 1
                    ]
                    results[i]["aks"] = predicted_aks if predicted_aks else ["QAG"]

            except Exception as e:
                print(f"Warning: Batch prediction error: {e}")

        return results

    def save(self, path: Optional[Path] = None, backend_name: Optional[str] = None):
        """Save model."""
        path = Path(path) if path else MODEL_DIR
        path.mkdir(parents=True, exist_ok=True)

        embedder = self.embedder
        embedder_repr = str(embedder) if embedder else "unknown"
        self.embedder = None

        self.backend_name = backend_name or "generic"
        self.embedder_info = embedder_repr

        filename = f"multilabel_classifier_{backend_name}.pkl" if backend_name else "multilabel_classifier.pkl"
        filepath = path / filename

        with open(filepath, "wb") as f:
            pickle.dump(self, f)

        self.embedder = embedder
        print(f"  Model saved to: {filepath}")

    @classmethod
    def load(cls, path: Optional[Path] = None, backend_name: Optional[str] = None):
        path = Path(path) if path else MODEL_DIR
        filename = f"multilabel_classifier_{backend_name}.pkl" if backend_name else "multilabel_classifier.pkl"
        filepath = path / filename

        with open(filepath, "rb") as f:
            return pickle.load(f)


# ============================================================================
# Data Loading
# ============================================================================


def load_training_data_multilabel(splits: list[str] = ["train", "validation"]):
    """Load training data with multi-label AKs."""
    texts = []
    relevance = []
    priorities = []
    aks_list = []

    for split in splits:
        filepath = DATA_DIR / f"{split}.jsonl"
        if not filepath.exists():
            print(f"Warning: {filepath} not found")
            continue

        with open(filepath) as f:
            for line in f:
                item = json.loads(line)
                inp = item.get("input", {})
                labels = item.get("labels", {})

                text = f"{inp.get('title', '')} {inp.get('content', '')}"
                texts.append(text)

                is_relevant = labels.get("relevant", False)
                relevance.append(1 if is_relevant else 0)
                priorities.append(labels.get("priority") or "none")

                # Get multi-label AKs (fall back to single 'ak' if 'aks' not present)
                aks = labels.get("aks", [])
                if not aks and labels.get("ak"):
                    aks = [labels.get("ak")]
                aks_list.append(aks)

    return texts, relevance, priorities, aks_list


def load_test_data_multilabel():
    """Load test data with multi-label AKs."""
    return load_training_data_multilabel(splits=["test"])


# ============================================================================
# Evaluation
# ============================================================================


def evaluate_multilabel(
    clf: MultilabelEmbeddingClassifier,
    texts: list[str],
    relevance: list[int],
    priorities: list[str],
    aks_list: list[list[str]],
) -> dict:
    """Evaluate multi-label classifier."""
    predictions = clf.predict_batch(texts)

    # Relevance (same as single-label)
    y_true_rel = np.array(relevance)
    y_pred_rel = np.array([1 if p["relevant"] else 0 for p in predictions])

    rel_acc = accuracy_score(y_true_rel, y_pred_rel)
    rel_f1 = f1_score(y_true_rel, y_pred_rel)

    print("\n=== RELEVANCE (binary) ===")
    print(classification_report(y_true_rel, y_pred_rel, target_names=["irrelevant", "relevant"]))
    print(f"Accuracy: {rel_acc:.1%}, F1: {rel_f1:.1%}")

    # Priority (same as single-label)
    relevant_mask = y_true_rel == 1
    priorities_arr = np.array(priorities)
    valid_priority = np.array([p in PRIORITY_LEVELS for p in priorities_arr])
    eval_mask = relevant_mask & valid_priority

    if np.sum(eval_mask) > 0:
        y_true_priority = priorities_arr[eval_mask]
        y_pred_priority = np.array([p["priority"] or "medium" for p in predictions])[eval_mask]

        priority_acc = accuracy_score(y_true_priority, y_pred_priority)
        print("\n=== PRIORITY (4-class) ===")
        print(classification_report(y_true_priority, y_pred_priority, labels=PRIORITY_LEVELS, zero_division=0))
        print(f"Accuracy: {priority_acc:.1%}")
    else:
        priority_acc = 0

    # AK (multi-label metrics)
    print("\n=== AK (multi-label) ===")

    # Filter to relevant items with valid AKs
    valid_ak_mask = np.array([any(ak in AK_CLASSES for ak in aks) for aks in aks_list])
    eval_mask = relevant_mask & valid_ak_mask

    if np.sum(eval_mask) > 0:
        # Get ground truth and predictions as binary matrices
        aks_true = [aks_list[i] for i in range(len(aks_list)) if eval_mask[i]]
        aks_pred = [predictions[i]["aks"] for i in range(len(predictions)) if eval_mask[i]]

        y_true_ak = create_multilabel_matrix(aks_true, AK_CLASSES)
        y_pred_ak = create_multilabel_matrix(aks_pred, AK_CLASSES)

        # Subset accuracy (exact match)
        subset_acc = accuracy_score(y_true_ak, y_pred_ak)

        # Hamming loss (fraction of wrong labels)
        h_loss = hamming_loss(y_true_ak, y_pred_ak)

        # Per-label accuracy
        print("Per-AK metrics:")
        for i, ak in enumerate(AK_CLASSES):
            if y_true_ak[:, i].sum() > 0:
                acc = accuracy_score(y_true_ak[:, i], y_pred_ak[:, i])
                f1 = f1_score(y_true_ak[:, i], y_pred_ak[:, i], zero_division=0)
                support = y_true_ak[:, i].sum()
                print(f"  {ak}: acc={acc:.1%}, f1={f1:.1%}, support={support}")

        print(f"\nSubset accuracy (exact match): {subset_acc:.1%}")
        print(f"Hamming loss: {h_loss:.3f}")

        # Partial match (at least one AK correct)
        partial_match = 0
        for true, pred in zip(aks_true, aks_pred):
            if set(true) & set(pred):  # Intersection
                partial_match += 1
        partial_acc = partial_match / len(aks_true)
        print(f"Partial match (>=1 correct): {partial_acc:.1%}")

        ak_acc = subset_acc
    else:
        ak_acc = 0
        h_loss = 1.0
        partial_acc = 0

    return {
        "relevance_accuracy": rel_acc,
        "relevance_f1": rel_f1,
        "priority_accuracy": priority_acc,
        "ak_subset_accuracy": ak_acc,
        "ak_hamming_loss": h_loss,
        "ak_partial_accuracy": partial_acc,
    }


# ============================================================================
# Main
# ============================================================================


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Train multi-label embedding classifier")
    parser.add_argument("--compare", action="store_true", help="Compare against single-label classifier")
    args = parser.parse_args()

    backend_name = os.environ.get("EMBEDDING_BACKEND", "nomic-v2")

    try:
        backend_config = get_backend_config(backend_name)
    except ValueError:
        print(f"Warning: Unknown backend '{backend_name}', using defaults")
        backend_config = {}

    print("=" * 60)
    print(f"Multi-Label Embedding Classifier - {backend_name}")
    print("=" * 60)
    print(f"  Status: EXPERIMENTAL")
    print(f"  Backend: {backend_config.get('model', 'default')}")

    # Load data
    print("\n[1/4] Loading data with multi-label AKs...")
    train_texts, train_rel, train_pri, train_aks = load_training_data_multilabel()
    test_texts, test_rel, test_pri, test_aks = load_test_data_multilabel()

    print(f"  Training: {len(train_texts)} items")
    print(f"  Test: {len(test_texts)} items")

    # Multi-label stats
    multi_ak_count = sum(1 for aks in train_aks if len(aks) > 1)
    print(f"  Multi-AK items in training: {multi_ak_count} ({multi_ak_count/len(train_aks)*100:.1f}%)")

    # AK distribution
    ak_counts = Counter()
    for aks in train_aks:
        for ak in aks:
            ak_counts[ak] += 1
    print(f"  AK distribution: {dict(ak_counts)}")

    # Train
    print("\n[2/4] Training multi-label classifier...")
    clf = MultilabelEmbeddingClassifier(backend_config=backend_config)
    clf.fit(train_texts, train_rel, train_pri, train_aks)

    # Evaluate
    print("\n[3/4] Evaluating on test set...")
    metrics = evaluate_multilabel(clf, test_texts, test_rel, test_pri, test_aks)

    # Speed benchmark
    print("\n[4/4] Speed benchmark...")
    clf.predict_batch(test_texts[:10])  # Warmup
    start = time.perf_counter()
    clf.predict_batch(test_texts)
    elapsed = time.perf_counter() - start
    speed = len(test_texts) / elapsed
    print(f"  Speed: {speed:.1f} items/sec")

    # Save
    print("\n=== Saving Model ===")
    clf.save(backend_name=backend_name)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY (Multi-Label)")
    print("=" * 60)
    print(f"Backend:                    {backend_name}")
    print(f"Relevance accuracy:         {metrics['relevance_accuracy']:.1%}")
    print(f"Priority accuracy:          {metrics['priority_accuracy']:.1%}")
    print(f"AK subset accuracy:         {metrics['ak_subset_accuracy']:.1%}")
    print(f"AK partial match:           {metrics['ak_partial_accuracy']:.1%}")
    print(f"AK hamming loss:            {metrics['ak_hamming_loss']:.3f}")
    print(f"Speed:                      {speed:.1f} items/sec")

    # Compare with single-label if requested
    if args.compare:
        print("\n" + "=" * 60)
        print("COMPARISON: Multi-Label vs Single-Label")
        print("=" * 60)

        try:
            from train_embedding_classifier import EmbeddingClassifier

            single_clf = EmbeddingClassifier.load(backend_name=backend_name)
            print(f"Loaded single-label classifier: {backend_name}")

            # Quick comparison on test set
            single_preds = single_clf.predict_batch(test_texts)

            # Compare AK predictions
            relevant_mask = np.array(test_rel) == 1
            correct_single = 0
            correct_multi = 0

            for i, (true_aks, is_rel) in enumerate(zip(test_aks, relevant_mask)):
                if not is_rel or not true_aks:
                    continue

                single_ak = single_preds[i].get("ak")
                multi_aks = clf.predict_batch([test_texts[i]])[0]["aks"]

                if single_ak in true_aks:
                    correct_single += 1
                if set(multi_aks) & set(true_aks):
                    correct_multi += 1

            n_relevant = sum(relevant_mask)
            print(f"\nOn {n_relevant} relevant test items:")
            print(f"  Single-label (any match): {correct_single/n_relevant:.1%}")
            print(f"  Multi-label (any match):  {correct_multi/n_relevant:.1%}")

        except Exception as e:
            print(f"Could not load single-label classifier for comparison: {e}")

    # Example predictions
    print("\n=== Example Predictions ===")
    examples = [
        ("Hessen kürzt Kita-Mittel um 50 Millionen Euro",
         "Die Landesregierung plant Kürzungen bei der Kinderbetreuung.",
         "hessenschau.de"),
        ("Die Kasseneinnahmen reichen: Durchleuchtet das Pflegebudget!",
         "Kritik an Budgetverwendung im Gesundheitswesen.",
         "FAZ"),
        ("Bildung statt Abschiebung – hessenweites Bündnis gegründet",
         "Bündnis fordert Bleiberecht für Geflüchtete in Ausbildung.",
         "fr.de"),
    ]

    for title, content, source in examples:
        pred = clf.predict(title, content, source)
        status = "RELEVANT" if pred["relevant"] else "irrelevant"
        print(f"\n  [{status}] {title[:50]}...")
        if pred["relevant"]:
            print(f"    priority: {pred['priority']}")
            print(f"    AKs: {pred['aks']}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
