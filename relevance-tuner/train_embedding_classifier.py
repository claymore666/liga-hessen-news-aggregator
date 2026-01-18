#!/usr/bin/env python3
"""
Liga Hessen Embedding-based Classifier

Uses Ollama embeddings (nomic-embed-text) for semantic understanding.

Benefits:
- "Kita" and "Kindergarten" are similar (semantic)
- "Pflege" and "Altenpflege" are similar
- Works better with limited training data
- 8192 token context (no content truncation needed)
- 768-dimensional embeddings

Model: nomic-embed-text:137m-v1.5-fp16 (via Ollama)

Usage:
    python train_embedding_classifier.py

Production:
    from train_embedding_classifier import EmbeddingClassifier
    clf = EmbeddingClassifier.load()
    result = clf.predict(title, content)
"""

import pickle
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.preprocessing import LabelEncoder

# Import from central config and utilities
from config import (
    AK_CLASSES,
    DATA_DIR,
    MODELS_DIR,
    PRIORITY_LEVELS,
    RANDOM_SEED,
    get_backend_config,
)
from utils import get_embedder, load_test_data, load_training_data

# ============================================================================
# Configuration
# ============================================================================

MODEL_DIR = MODELS_DIR / "embedding"


# ============================================================================
# Embedding Classifier
# ============================================================================


class EmbeddingClassifier:
    """
    Hierarchical classifier using embeddings.

    Stage 1: Relevance (binary)
    Stage 2: Priority (4-class, only for relevant)
    Stage 3: AK (6-class, only for relevant)

    Supports multiple embedding backends with backend-specific
    classifier configurations.
    """

    def __init__(self, backend_config: Optional[dict] = None):
        """
        Initialize classifier with backend-specific settings.

        Args:
            backend_config: Configuration dict from get_backend_config().
                           If None, uses default settings.
        """
        self.embedder = None  # Lazy load
        self.backend_config = backend_config or {}

        # Get classifier settings from config (with defaults)
        lr_c = self.backend_config.get("lr_c", 1.0)
        lr_max_iter = self.backend_config.get("lr_max_iter", 1000)
        rf_n_estimators = self.backend_config.get("rf_n_estimators", 200)
        rf_max_depth = self.backend_config.get("rf_max_depth", 15)

        # Stage 1: Relevance
        self.relevance_clf = LogisticRegression(
            max_iter=lr_max_iter,
            class_weight="balanced",
            C=lr_c,
            random_state=RANDOM_SEED,
        )

        # Stage 2: Priority (RandomForest works well with embeddings)
        self.priority_clf = RandomForestClassifier(
            n_estimators=rf_n_estimators,
            max_depth=rf_max_depth,
            class_weight="balanced",
            random_state=RANDOM_SEED,
            n_jobs=-1,
        )
        self.priority_encoder = LabelEncoder()

        # Stage 3: AK
        self.ak_clf = RandomForestClassifier(
            n_estimators=rf_n_estimators,
            max_depth=rf_max_depth,
            class_weight="balanced",
            random_state=RANDOM_SEED,
            n_jobs=-1,
        )
        self.ak_encoder = LabelEncoder()

        self.is_fitted = False

    def _load_embedder(self):
        """Lazy load the embedding model."""
        if self.embedder is None:
            print("  Loading embedder...")
            self.embedder = get_embedder()  # Uses EMBEDDING_BACKEND env var
            print(f"  Backend: {self.embedder}")
        return self.embedder

    def _embed(self, texts: list[str], show_progress: bool = True) -> np.ndarray:
        """Embed texts using OllamaEmbedder."""
        embedder = self._load_embedder()
        embeddings = embedder.encode(texts, show_progress_bar=show_progress)
        return np.array(embeddings)

    def fit(
        self,
        texts: list[str],
        relevance: list[int],
        priorities: list[str],
        aks: list[str],
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

        # Stage 3: AK (only on relevant)
        print("  Training AK classifier...")
        aks_arr = np.array(aks)
        valid_ak = np.array([a in AK_CLASSES for a in aks_arr])
        valid_mask = relevant_mask & valid_ak

        if np.sum(valid_mask) > 10:
            X_ak = X[valid_mask]
            y_ak = aks_arr[valid_mask]
            self.ak_encoder.fit(AK_CLASSES)
            y_ak_enc = self.ak_encoder.transform(y_ak)
            self.ak_clf.fit(X_ak, y_ak_enc)

        self.is_fitted = True
        print("  Training complete!")

    def predict(self, title: str, content: str, source: Optional[str] = None) -> dict:
        """Predict for a single item."""
        # No truncation needed - OllamaEmbedder handles long texts via chunking
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
            "ak": None,
            "ak_confidence": None,
        }

        if is_relevant:
            # Stage 2: Priority
            try:
                priority_prob = self.priority_clf.predict_proba(X)[0]
                priority_idx = np.argmax(priority_prob)
                result["priority"] = self.priority_encoder.inverse_transform(
                    [priority_idx]
                )[0]
                result["priority_confidence"] = float(priority_prob[priority_idx])
            except Exception:
                result["priority"] = "medium"
                result["priority_confidence"] = 0.5

            # Stage 3: AK
            try:
                ak_prob = self.ak_clf.predict_proba(X)[0]
                ak_idx = np.argmax(ak_prob)
                result["ak"] = self.ak_encoder.inverse_transform([ak_idx])[0]
                result["ak_confidence"] = float(ak_prob[ak_idx])
            except Exception:
                result["ak"] = "QAG"
                result["ak_confidence"] = 0.5

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
                "ak": None,
                "ak_confidence": None,
            }
            results.append(result)

        # Stage 2 & 3: Only for relevant items
        relevant_indices = [i for i, r in enumerate(results) if r["relevant"]]
        if relevant_indices:
            X_relevant = X[relevant_indices]

            try:
                priority_probs = self.priority_clf.predict_proba(X_relevant)
                ak_probs = self.ak_clf.predict_proba(X_relevant)

                for j, i in enumerate(relevant_indices):
                    priority_idx = np.argmax(priority_probs[j])
                    results[i]["priority"] = self.priority_encoder.inverse_transform(
                        [priority_idx]
                    )[0]
                    results[i]["priority_confidence"] = float(
                        priority_probs[j, priority_idx]
                    )

                    ak_idx = np.argmax(ak_probs[j])
                    results[i]["ak"] = self.ak_encoder.inverse_transform([ak_idx])[0]
                    results[i]["ak_confidence"] = float(ak_probs[j, ak_idx])
            except Exception as e:
                print(f"Warning: {e}")

        return results

    def save(self, path: Optional[Path] = None, backend_name: Optional[str] = None):
        """
        Save model with backend-specific filename.

        Saves as dict format for compatibility with classifier-api.
        Automatically backs up existing model before overwriting.

        Args:
            path: Directory to save to (default: MODEL_DIR)
            backend_name: Backend identifier for filename (e.g., "bge-m3", "sentence-transformers")
                         If None, uses generic name
        """
        from datetime import datetime
        import shutil

        path = Path(path) if path else MODEL_DIR
        path.mkdir(parents=True, exist_ok=True)

        # Backend-specific filename
        if backend_name:
            filename = f"embedding_classifier_{backend_name}.pkl"
        else:
            filename = "embedding_classifier.pkl"

        filepath = path / filename

        # Backup existing model if it exists
        if filepath.exists():
            backup_dir = path / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = backup_dir / f"{filename}.{timestamp}"
            shutil.copy2(filepath, backup_path)
            print(f"  Backed up existing model to: {backup_path}")

        # Save as dict format (compatible with classifier-api)
        data = {
            "relevance_clf": self.relevance_clf,
            "priority_clf": self.priority_clf,
            "ak_clf": self.ak_clf,
            "priority_encoder": self.priority_encoder,
            "ak_encoder": self.ak_encoder,
            "backend": backend_name or "generic",
            "multilabel": False,  # Single-label for now
        }

        with open(filepath, "wb") as f:
            pickle.dump(data, f)

        print(f"  Model saved to: {filepath}")

    @classmethod
    def load(cls, path: Optional[Path] = None, backend_name: Optional[str] = None) -> "EmbeddingClassifier":
        """
        Load model, optionally for a specific backend.

        Args:
            path: Directory to load from (default: MODEL_DIR)
            backend_name: Backend identifier (e.g., "bge-m3"). If None, loads generic.
        """
        path = Path(path) if path else MODEL_DIR

        if backend_name:
            filename = f"embedding_classifier_{backend_name}.pkl"
        else:
            filename = "embedding_classifier.pkl"

        filepath = path / filename
        with open(filepath, "rb") as f:
            clf = pickle.load(f)
        return clf

    @classmethod
    def list_available(cls, path: Optional[Path] = None) -> list[str]:
        """List available saved models."""
        path = Path(path) if path else MODEL_DIR
        models = []
        for f in path.glob("embedding_classifier_*.pkl"):
            backend = f.stem.replace("embedding_classifier_", "")
            models.append(backend)
        return models


# ============================================================================
# Evaluation
# ============================================================================


def evaluate(
    clf: EmbeddingClassifier,
    texts: list[str],
    relevance: list[int],
    priorities: list[str],
    aks: list[str],
) -> dict:
    """Evaluate the classifier."""
    predictions = clf.predict_batch(texts)

    # Relevance
    y_true_rel = np.array(relevance)
    y_pred_rel = np.array([1 if p["relevant"] else 0 for p in predictions])

    rel_acc = accuracy_score(y_true_rel, y_pred_rel)
    rel_f1 = f1_score(y_true_rel, y_pred_rel)

    print("\n=== RELEVANCE (binary) ===")
    print(
        classification_report(
            y_true_rel, y_pred_rel, target_names=["irrelevant", "relevant"]
        )
    )
    print(f"Accuracy: {rel_acc:.1%}, F1: {rel_f1:.1%}")

    # Priority (on true relevant)
    relevant_mask = y_true_rel == 1
    priorities_arr = np.array(priorities)
    valid_priority = np.array([p in PRIORITY_LEVELS for p in priorities_arr])
    eval_mask = relevant_mask & valid_priority

    if np.sum(eval_mask) > 0:
        y_true_priority = priorities_arr[eval_mask]
        y_pred_priority = np.array([p["priority"] or "medium" for p in predictions])[
            eval_mask
        ]

        priority_acc = accuracy_score(y_true_priority, y_pred_priority)
        print("\n=== PRIORITY (4-class) ===")
        print(
            classification_report(
                y_true_priority,
                y_pred_priority,
                labels=PRIORITY_LEVELS,
                zero_division=0,
            )
        )
        print(f"Accuracy: {priority_acc:.1%}")

        # Within-1-level
        level_map = {p: i for i, p in enumerate(PRIORITY_LEVELS)}
        within_one = np.mean(
            [
                abs(level_map[t] - level_map.get(p, 1)) <= 1
                for t, p in zip(y_true_priority, y_pred_priority)
            ]
        )
        print(f"Within-1-level: {within_one:.1%}")
    else:
        priority_acc = 0
        within_one = 0

    # AK (on true relevant)
    aks_arr = np.array(aks)
    valid_ak = np.array([a in AK_CLASSES for a in aks_arr])
    eval_mask = relevant_mask & valid_ak

    if np.sum(eval_mask) > 0:
        y_true_ak = aks_arr[eval_mask]
        y_pred_ak = np.array([p["ak"] or "QAG" for p in predictions])[eval_mask]

        ak_acc = accuracy_score(y_true_ak, y_pred_ak)
        print("\n=== AK (6-class) ===")
        print(
            classification_report(
                y_true_ak, y_pred_ak, labels=AK_CLASSES, zero_division=0
            )
        )
        print(f"Accuracy: {ak_acc:.1%}")
    else:
        ak_acc = 0

    return {
        "relevance_accuracy": rel_acc,
        "relevance_f1": rel_f1,
        "priority_accuracy": priority_acc,
        "priority_within_one": within_one,
        "ak_accuracy": ak_acc,
    }


# ============================================================================
# Main
# ============================================================================


def main():
    import os

    # Get backend from environment
    backend_name = os.environ.get("EMBEDDING_BACKEND", "sentence-transformers")

    # Get backend-specific configuration
    try:
        backend_config = get_backend_config(backend_name)
    except ValueError:
        print(f"Warning: Unknown backend '{backend_name}', using defaults")
        backend_config = {}

    print("=" * 60)
    print(f"Liga Embedding Classifier - {backend_name}")
    print("=" * 60)
    print(f"  Status: {backend_config.get('status', 'unknown')}")
    print(f"  Model: {backend_config.get('model', 'default')}")
    print(f"  RF: n_estimators={backend_config.get('rf_n_estimators', 200)}, "
          f"max_depth={backend_config.get('rf_max_depth', 15)}")

    # Load data using shared utilities
    print("\n[1/4] Loading data...")
    train_texts, train_rel, train_pri, train_ak = load_training_data(
        splits=["train", "validation"]
    )
    test_texts, test_rel, test_pri, test_ak = load_test_data()

    print(f"  Training: {len(train_texts)} items")
    print(f"  Test: {len(test_texts)} items")

    rel_count = sum(train_rel)
    print(f"  Relevant: {rel_count} ({rel_count/len(train_rel)*100:.1f}%)")

    pri_dist = Counter(
        [p for p, r in zip(train_pri, train_rel) if r and p in PRIORITY_LEVELS]
    )
    print(f"  Priority: {dict(pri_dist)}")

    ak_dist = Counter(
        [a for a, r in zip(train_ak, train_rel) if r and a in AK_CLASSES]
    )
    print(f"  AK: {dict(ak_dist)}")

    # Train with backend-specific config
    print("\n[2/4] Training classifier...")
    clf = EmbeddingClassifier(backend_config=backend_config)
    clf.fit(train_texts, train_rel, train_pri, train_ak)

    # Evaluate
    print("\n[3/4] Evaluating on test set...")
    metrics = evaluate(clf, test_texts, test_rel, test_pri, test_ak)

    # Speed benchmark
    print("\n[4/4] Speed benchmark...")
    # Warmup
    clf.predict_batch(test_texts[:10])

    start = time.perf_counter()
    clf.predict_batch(test_texts)
    elapsed = time.perf_counter() - start
    speed = len(test_texts) / elapsed
    print(f"  Speed: {speed:.1f} items/sec ({1000/speed:.1f}ms per item)")
    print("  Note: Embedding is the bottleneck, sklearn prediction is instant")

    # Save with backend-specific filename
    print("\n=== Saving Model ===")
    clf.save(backend_name=backend_name)

    # Save metrics for comparison (append to history)
    import json
    import hashlib
    from datetime import datetime

    # Compute model fingerprint (MD5 hash of saved model file)
    model_file = MODEL_DIR / f"embedding_classifier_{backend_name}.pkl"
    with open(model_file, "rb") as f:
        model_fingerprint = hashlib.md5(f.read()).hexdigest()[:12]
    print(f"  Model fingerprint: {model_fingerprint}")

    metrics_file = MODEL_DIR / "metrics.json"
    all_metrics = {}
    if metrics_file.exists():
        with open(metrics_file) as f:
            all_metrics = json.load(f)

    # New entry for this training run
    new_entry = {
        "model_fingerprint": model_fingerprint,
        "relevance_accuracy": round(metrics["relevance_accuracy"], 4),
        "priority_accuracy": round(metrics["priority_accuracy"], 4),
        "priority_within_one": round(metrics["priority_within_one"], 4),
        "ak_accuracy": round(metrics["ak_accuracy"], 4),
        "speed_items_per_sec": round(speed, 1),
        "timestamp": datetime.now().isoformat(),
        "train_size": len(train_texts),
        "test_size": len(test_texts),
    }

    # Migrate old format (single entry) to new format (list of entries)
    if backend_name in all_metrics:
        existing = all_metrics[backend_name]
        if isinstance(existing, dict):
            # Old format: convert to list
            all_metrics[backend_name] = [existing, new_entry]
        else:
            # New format: append to list
            all_metrics[backend_name].append(new_entry)
    else:
        all_metrics[backend_name] = [new_entry]

    with open(metrics_file, "w") as f:
        json.dump(all_metrics, f, indent=2)
    print(f"  Metrics saved to: {metrics_file}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Backend:                {backend_name}")
    print(f"Relevance accuracy:     {metrics['relevance_accuracy']:.1%}")
    print(f"Priority accuracy:      {metrics['priority_accuracy']:.1%}")
    print(f"Priority within-1:      {metrics['priority_within_one']:.1%}")
    print(f"AK accuracy:            {metrics['ak_accuracy']:.1%}")
    print(f"Speed:                  {speed:.1f} items/sec")

    # Show all backends comparison (using latest entry from each)
    if len(all_metrics) > 1:
        print("\n=== ALL BACKENDS COMPARISON (latest) ===")
        print(f"{'Backend':<25} {'Relevance':>10} {'AK':>10} {'Speed':>12}")
        print("-" * 60)

        def get_latest(entries):
            """Get latest entry from list or single dict."""
            if isinstance(entries, list):
                return entries[-1]
            return entries  # Old format fallback

        sorted_backends = sorted(
            all_metrics.items(),
            key=lambda x: -get_latest(x[1])["relevance_accuracy"]
        )
        for name, entries in sorted_backends:
            m = get_latest(entries)
            print(f"{name:<25} {m['relevance_accuracy']:>9.1%} {m['ak_accuracy']:>9.1%} {m['speed_items_per_sec']:>9.1f}/s")

    # Show history for current backend
    if backend_name in all_metrics:
        entries = all_metrics[backend_name]
        if isinstance(entries, list) and len(entries) > 1:
            print(f"\n=== {backend_name.upper()} HISTORY ===")
            print(f"{'Date':<20} {'Relevance':>10} {'Priority':>10} {'AK':>10} {'Train':>8}")
            print("-" * 65)
            for entry in entries:
                date = entry["timestamp"][:10]
                train = entry.get("train_size", "?")
                print(f"{date:<20} {entry['relevance_accuracy']:>9.1%} {entry['priority_accuracy']:>9.1%} {entry['ak_accuracy']:>9.1%} {train:>8}")

    # Examples
    print("\n=== Example Predictions ===")
    examples = [
        (
            "Hessen kürzt Kita-Mittel um 50 Millionen Euro",
            "Die Landesregierung plant Kürzungen bei der Kinderbetreuung.",
            "hessenschau.de",
        ),
        (
            "Champions League: Bayern München gewinnt",
            "Mit 3:0 siegte Bayern gegen den italienischen Meister.",
            "sport1.de",
        ),
        (
            "Neue Pflegeheime brauchen mehr Personal",
            "Das Sozialministerium fordert bessere Personalausstattung in der Altenpflege.",
            "tagesschau.de",
        ),
        (
            "Flüchtlingsunterkünfte in Frankfurt überfüllt",
            "Die Erstaufnahme meldet Kapazitätsengpässe bei der Unterbringung.",
            "fr.de",
        ),
    ]

    for title, content, source in examples:
        pred = clf.predict(title, content, source)
        status = "RELEVANT" if pred["relevant"] else "irrelevant"
        print(f"\n  [{status}] {title[:45]}...")
        print(f"    relevance: {pred['relevance_confidence']:.0%}")
        if pred["relevant"]:
            print(f"    priority: {pred['priority']} ({pred['priority_confidence']:.0%})")
            print(f"    ak: {pred['ak']} ({pred['ak_confidence']:.0%})")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
