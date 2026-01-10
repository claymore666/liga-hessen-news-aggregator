#!/usr/bin/env python3
"""
Liga Hessen ML Classifiers - Hierarchical Approach

Three separate classifiers:
1. Relevance: Is this news item relevant to Liga? (binary)
2. Priority: How urgent is this? (4-class: low/medium/high/critical)
3. AK: Which working group? (6-class: AK1-5, QAG)

Hierarchical: Priority and AK are only predicted for relevant items.

Target accuracy:
- Relevance: 90-95%
- Priority: 80-95%
- AK: 80-95%
"""

import pickle
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.preprocessing import LabelEncoder

# Import from central config and utilities
from config import (
    AK_CLASSES,
    IRRELEVANT_KEYWORDS,
    LIGA_KEYWORDS,
    LR_C,
    LR_MAX_ITER,
    MODELS_DIR,
    PRIORITY_LEVELS,
    RANDOM_SEED,
    RF_MAX_DEPTH,
    RF_N_ESTIMATORS,
    TFIDF_MAX_DF,
    TFIDF_MAX_FEATURES,
    TFIDF_MIN_DF,
    TFIDF_NGRAM_RANGE,
)
from utils import load_test_data, load_training_data

# ============================================================================
# Configuration
# ============================================================================

MODEL_DIR = MODELS_DIR / "liga_ml"


# ============================================================================
# Feature Engineering
# ============================================================================

def count_keywords(text: str, keywords: list[str]) -> int:
    """Count keyword occurrences."""
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw in text_lower)


def extract_features(texts: list[str]) -> np.ndarray:
    """Extract custom features beyond TF-IDF."""
    features = []
    for text in texts:
        text_lower = text.lower()
        features.append([
            count_keywords(text, LIGA_KEYWORDS),
            count_keywords(text, IRRELEVANT_KEYWORDS),  # Sports, celebrities, etc.
            len(text),
            text_lower.count("hessen"),
            1 if "liga" in text_lower else 0,
            1 if "wohlfahrt" in text_lower else 0,
        ])
    return np.array(features, dtype=np.float32)


# ============================================================================
# Main Classifier Class
# ============================================================================

class LigaMLClassifier:
    """
    Hierarchical ML classifier for Liga news.

    Stage 1: Relevance (binary) - trained on all data
    Stage 2: Priority (4-class) - trained only on relevant items
    Stage 3: AK (6-class) - trained only on relevant items
    """

    def __init__(self):
        # Shared TF-IDF vectorizer (config values)
        self.tfidf = TfidfVectorizer(
            max_features=TFIDF_MAX_FEATURES,
            ngram_range=TFIDF_NGRAM_RANGE,
            min_df=TFIDF_MIN_DF,
            max_df=TFIDF_MAX_DF,
            sublinear_tf=True,
        )

        # Stage 1: Relevance classifier (binary)
        self.relevance_clf = LogisticRegression(
            max_iter=LR_MAX_ITER,
            class_weight="balanced",
            C=LR_C,
            random_state=RANDOM_SEED,
        )

        # Stage 2: Priority classifier (multi-class)
        self.priority_clf = RandomForestClassifier(
            n_estimators=RF_N_ESTIMATORS,
            max_depth=RF_MAX_DEPTH,
            class_weight="balanced",
            random_state=RANDOM_SEED,
            n_jobs=-1,
        )
        self.priority_encoder = LabelEncoder()

        # Stage 3: AK classifier (multi-class)
        self.ak_clf = RandomForestClassifier(
            n_estimators=RF_N_ESTIMATORS,
            max_depth=RF_MAX_DEPTH,
            class_weight="balanced",
            random_state=RANDOM_SEED,
            n_jobs=-1,
        )
        self.ak_encoder = LabelEncoder()

        self.is_fitted = False

    def _get_features(self, texts: list[str], fit: bool = False) -> np.ndarray:
        """Get combined TF-IDF and custom features."""
        if fit:
            tfidf_features = self.tfidf.fit_transform(texts).toarray()
        else:
            tfidf_features = self.tfidf.transform(texts).toarray()

        custom_features = extract_features(texts)
        return np.hstack([tfidf_features, custom_features])

    def fit(
        self,
        texts: list[str],
        relevance: list[int],
        priorities: list[str],
        aks: list[str],
    ):
        """
        Train all three classifiers.

        Args:
            texts: List of text strings (title + content)
            relevance: List of 0/1 (irrelevant/relevant)
            priorities: List of priority labels (for relevant items, "none" for irrelevant)
            aks: List of AK labels (for relevant items, "none" for irrelevant)
        """
        print("  Extracting features...")
        X = self._get_features(texts, fit=True)
        print(f"  Feature matrix: {X.shape}")

        # Stage 1: Relevance
        print("  Training relevance classifier...")
        y_rel = np.array(relevance)
        self.relevance_clf.fit(X, y_rel)

        # Stage 2: Priority (only on relevant items)
        print("  Training priority classifier...")
        relevant_mask = y_rel == 1
        X_relevant = X[relevant_mask]

        # Filter to valid priorities
        priorities_arr = np.array(priorities)
        valid_priority = np.array([p in PRIORITY_LEVELS for p in priorities_arr])
        valid_mask = relevant_mask & valid_priority

        if np.sum(valid_mask) > 10:
            X_priority = X[valid_mask]
            y_priority = priorities_arr[valid_mask]
            self.priority_encoder.fit(PRIORITY_LEVELS)
            y_priority_enc = self.priority_encoder.transform(y_priority)
            self.priority_clf.fit(X_priority, y_priority_enc)
        else:
            print("    Warning: Not enough priority data!")

        # Stage 3: AK (only on relevant items)
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
        else:
            print("    Warning: Not enough AK data!")

        self.is_fitted = True
        print("  Training complete!")

    def predict(self, title: str, content: str, source: Optional[str] = None) -> dict:
        """Predict for a single item."""
        # TF-IDF handles any text length
        text = f"{title} {content}"
        if source:
            text += f" Quelle: {source}"

        X = self._get_features([text])

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
                result["priority"] = self.priority_encoder.inverse_transform([priority_idx])[0]
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
        X = self._get_features(texts)

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
                    # Priority
                    priority_idx = np.argmax(priority_probs[j])
                    results[i]["priority"] = self.priority_encoder.inverse_transform([priority_idx])[0]
                    results[i]["priority_confidence"] = float(priority_probs[j, priority_idx])

                    # AK
                    ak_idx = np.argmax(ak_probs[j])
                    results[i]["ak"] = self.ak_encoder.inverse_transform([ak_idx])[0]
                    results[i]["ak_confidence"] = float(ak_probs[j, ak_idx])
            except Exception as e:
                print(f"Warning: {e}")
                for i in relevant_indices:
                    results[i]["priority"] = "medium"
                    results[i]["priority_confidence"] = 0.5
                    results[i]["ak"] = "QAG"
                    results[i]["ak_confidence"] = 0.5

        return results

    def save(self, path: Optional[Path] = None):
        """Save model."""
        path = Path(path) if path else MODEL_DIR
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "liga_classifier.pkl", "wb") as f:
            pickle.dump(self, f)
        print(f"  Model saved to: {path / 'liga_classifier.pkl'}")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "LigaMLClassifier":
        """Load model."""
        path = Path(path) if path else MODEL_DIR
        with open(path / "liga_classifier.pkl", "rb") as f:
            return pickle.load(f)


# ============================================================================
# Evaluation
# ============================================================================

def evaluate(clf: LigaMLClassifier, texts: list[str], relevance: list[int],
             priorities: list[str], aks: list[str]) -> dict:
    """Evaluate the classifier."""
    predictions = clf.predict_batch(texts)

    # Relevance evaluation
    y_true_rel = np.array(relevance)
    y_pred_rel = np.array([1 if p["relevant"] else 0 for p in predictions])

    rel_acc = accuracy_score(y_true_rel, y_pred_rel)
    rel_f1 = f1_score(y_true_rel, y_pred_rel)

    print("\n=== RELEVANCE (binary) ===")
    print(classification_report(y_true_rel, y_pred_rel, target_names=["irrelevant", "relevant"]))
    print(f"Accuracy: {rel_acc:.1%}")

    # Priority evaluation (only on true relevant items)
    relevant_mask = y_true_rel == 1
    priorities_arr = np.array(priorities)
    valid_priority = np.array([p in PRIORITY_LEVELS for p in priorities_arr])
    eval_mask = relevant_mask & valid_priority

    if np.sum(eval_mask) > 0:
        y_true_priority = priorities_arr[eval_mask]
        y_pred_priority = np.array([p["priority"] or "medium" for p in predictions])[eval_mask]

        priority_acc = accuracy_score(y_true_priority, y_pred_priority)
        print("\n=== PRIORITY (4-class, on relevant items) ===")
        print(classification_report(y_true_priority, y_pred_priority, labels=PRIORITY_LEVELS, zero_division=0))
        print(f"Accuracy: {priority_acc:.1%}")

        # Within-1-level accuracy
        level_map = {p: i for i, p in enumerate(PRIORITY_LEVELS)}
        within_one = np.mean([
            abs(level_map[t] - level_map.get(p, 1)) <= 1
            for t, p in zip(y_true_priority, y_pred_priority)
        ])
        print(f"Within-1-level: {within_one:.1%}")
    else:
        priority_acc = 0
        within_one = 0

    # AK evaluation (only on true relevant items)
    aks_arr = np.array(aks)
    valid_ak = np.array([a in AK_CLASSES for a in aks_arr])
    eval_mask = relevant_mask & valid_ak

    if np.sum(eval_mask) > 0:
        y_true_ak = aks_arr[eval_mask]
        y_pred_ak = np.array([p["ak"] or "QAG" for p in predictions])[eval_mask]

        ak_acc = accuracy_score(y_true_ak, y_pred_ak)
        print("\n=== AK (6-class, on relevant items) ===")
        print(classification_report(y_true_ak, y_pred_ak, labels=AK_CLASSES, zero_division=0))
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
    print("=" * 60)
    print("Liga ML Classifier - Hierarchical Training (TF-IDF)")
    print("=" * 60)

    # Load data using shared utilities
    print("\n[1/4] Loading data...")
    train_texts, train_rel, train_pri, train_ak = load_training_data(
        splits=["train", "validation"]
    )
    test_texts, test_rel, test_pri, test_ak = load_test_data()

    print(f"  Training: {len(train_texts)} items")
    print(f"  Test: {len(test_texts)} items")

    # Show distribution
    rel_count = sum(train_rel)
    print(f"\n  Relevance: {rel_count} relevant ({rel_count/len(train_rel)*100:.1f}%), "
          f"{len(train_rel)-rel_count} irrelevant ({(len(train_rel)-rel_count)/len(train_rel)*100:.1f}%)")

    pri_dist = Counter([p for p, r in zip(train_pri, train_rel) if r and p in PRIORITY_LEVELS])
    print(f"  Priority: {dict(pri_dist)}")

    ak_dist = Counter([a for a, r in zip(train_ak, train_rel) if r and a in AK_CLASSES])
    print(f"  AK: {dict(ak_dist)}")

    # Train
    print("\n[2/4] Training classifiers...")
    clf = LigaMLClassifier()
    clf.fit(train_texts, train_rel, train_pri, train_ak)

    # Evaluate
    print("\n[3/4] Evaluating on test set...")
    metrics = evaluate(clf, test_texts, test_rel, test_pri, test_ak)

    # Speed benchmark
    print("\n[4/4] Speed benchmark...")
    start = time.perf_counter()
    for _ in range(3):
        clf.predict_batch(test_texts)
    elapsed = (time.perf_counter() - start) / 3
    speed = len(test_texts) / elapsed
    print(f"  Speed: {speed:.0f} items/sec ({1000/speed:.2f}ms per item)")

    # Save
    print("\n=== Saving Model ===")
    clf.save()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Relevance accuracy:     {metrics['relevance_accuracy']:.1%}")
    print(f"Priority accuracy:      {metrics['priority_accuracy']:.1%}")
    print(f"Priority within-1:      {metrics['priority_within_one']:.1%}")
    print(f"AK accuracy:            {metrics['ak_accuracy']:.1%}")
    print(f"Speed:                  {speed:.0f} items/sec")
    print(f"\nModel saved to: {MODEL_DIR}")

    # Examples
    print("\n=== Example Predictions ===")
    examples = [
        ("Hessen kürzt Kita-Mittel um 50 Millionen Euro",
         "Die Landesregierung plant Kürzungen bei der Kinderbetreuung. Sozialminister warnt vor Folgen.",
         "hessenschau.de"),
        ("Champions League: Bayern München gewinnt",
         "Mit 3:0 siegte Bayern gegen den italienischen Meister.",
         "sport1.de"),
        ("Pflegereform: Mehr Personal für hessische Altenheime",
         "Der Landtag beschließt bessere Personalausstattung in der Altenpflege.",
         "tagesschau.de"),
        ("Stellungnahme zur Migration gefordert",
         "Die Liga der Freien Wohlfahrtspflege fordert Stellungnahme zur Asylpolitik.",
         "liga-hessen.de"),
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
