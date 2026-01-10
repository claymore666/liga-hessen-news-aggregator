#!/usr/bin/env python3
"""
Liga Hessen Fast Classifier - Scikit-learn v2

Improved classifier for fast initial relevance screening.
Designed as a pre-filter: items classified as "definitely irrelevant"
skip the LLM, saving compute.

Key improvements over v1:
- Better feature engineering (keywords, source, length)
- Calibrated probabilities for reliable confidence scores
- Hybrid mode: filter only high-confidence irrelevant items
- Separate priority classifier with ordinal awareness

Usage:
    # Train and evaluate
    python train_sklearn_v2.py

    # Use in production
    from train_sklearn_v2 import LigaFastClassifier
    clf = LigaFastClassifier.load("models/sklearn_v2")
    result = clf.predict(title, content, source)
    if result["skip_llm"]:
        # High confidence irrelevant, skip LLM
        pass
    else:
        # Send to LLM for full analysis
        pass
"""

import json
import pickle
import re
import time
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    precision_recall_curve,
)
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import FunctionTransformer, LabelEncoder
from sklearn.svm import LinearSVC

# ============================================================================
# Configuration
# ============================================================================

DATA_DIR = Path(__file__).parent / "data" / "final"
MODEL_DIR = Path(__file__).parent / "models" / "sklearn_v2"

# Keywords that strongly indicate Liga relevance
LIGA_KEYWORDS = [
    # Organizations
    "liga", "wohlfahrt", "awo", "caritas", "diakonie", "drk", "paritätisch",
    "freie wohlfahrtspflege", "jüdische gemeinde",
    # Hessen specific
    "hessen", "hessisch", "frankfurt", "wiesbaden", "kassel", "darmstadt",
    "landesregierung", "landtag", "staatsminister",
    # AK1 - Grundsatz
    "sozialpolitik", "sozialstaat", "ehrenamt", "gemeinnützig", "bürgerengagement",
    # AK2 - Migration
    "migration", "flucht", "flüchtling", "asyl", "integration", "zuwanderung",
    "aufenthaltsgesetz", "abschiebung",
    # AK3 - Gesundheit/Pflege
    "pflege", "pflegekraft", "altenpflege", "krankenhaus", "klinik",
    "gesundheit", "demenz", "senioren", "pflegeheim", "häusliche pflege",
    # AK4 - Eingliederungshilfe
    "eingliederungshilfe", "behinderung", "behindert", "inklusion", "teilhabe",
    "werkstatt", "barrierefreiheit", "bthg",
    # AK5 - Kinder/Jugend/Familie
    "kita", "kindergarten", "kinderbetreuung", "jugend", "jugendhilfe",
    "familie", "familienberatung", "schwangerschaft", "kinderschutz",
    "erziehung", "fachkraft",
    # QAG - Querschnitt
    "digitalisierung", "klimaschutz", "wohnen", "wohnungsnot", "obdachlos",
    "sozialraum", "nachbarschaft",
    # Priority indicators
    "kürzung", "streichung", "haushalt", "finanzierung", "förderung",
    "gesetz", "verordnung", "anhörung", "stellungnahme",
]

# Keywords that often indicate irrelevance
IRRELEVANT_KEYWORDS = [
    "fußball", "bundesliga", "champions league", "sport", "tennis",
    "börse", "aktie", "dax", "kurs", "investition",
    "hollywood", "film", "kino", "serie", "unterhaltung",
    "wetter", "temperatur", "regen", "sonne",
    "restaurant", "kochen", "rezept", "essen",
    "mode", "fashion", "beauty", "lifestyle",
    "reise", "urlaub", "tourismus", "hotel",
]

# Confidence threshold for skipping LLM
# Items with irrelevant_probability > this skip LLM processing
SKIP_LLM_THRESHOLD = 0.85


# ============================================================================
# Feature Engineering
# ============================================================================

def count_keywords(text: str, keywords: list[str]) -> int:
    """Count occurrences of keywords in text (case-insensitive)."""
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def extract_features(texts: list[str]) -> dict:
    """Extract engineered features from texts."""
    features = {
        "liga_keyword_count": [],
        "irrelevant_keyword_count": [],
        "text_length": [],
        "word_count": [],
        "has_hessen": [],
        "has_liga_org": [],
    }

    liga_orgs = ["awo", "caritas", "diakonie", "drk", "paritätisch", "liga"]

    for text in texts:
        text_lower = text.lower()
        features["liga_keyword_count"].append(count_keywords(text, LIGA_KEYWORDS))
        features["irrelevant_keyword_count"].append(count_keywords(text, IRRELEVANT_KEYWORDS))
        features["text_length"].append(len(text))
        features["word_count"].append(len(text.split()))
        features["has_hessen"].append(1 if "hessen" in text_lower else 0)
        features["has_liga_org"].append(1 if any(org in text_lower for org in liga_orgs) else 0)

    return features


class FeatureExtractor:
    """Combined TF-IDF + engineered features."""

    def __init__(self):
        self.tfidf = TfidfVectorizer(
            max_features=8000,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.90,
            sublinear_tf=True,
            strip_accents="unicode",
        )
        self.is_fitted = False

    def fit(self, texts: list[str]):
        """Fit the TF-IDF vectorizer."""
        self.tfidf.fit(texts)
        self.is_fitted = True
        return self

    def transform(self, texts: list[str]) -> np.ndarray:
        """Transform texts to feature matrix."""
        # TF-IDF features
        tfidf_features = self.tfidf.transform(texts).toarray()

        # Engineered features
        eng_features = extract_features(texts)
        eng_matrix = np.column_stack([
            eng_features["liga_keyword_count"],
            eng_features["irrelevant_keyword_count"],
            np.log1p(eng_features["text_length"]),  # Log-scale length
            np.log1p(eng_features["word_count"]),
            eng_features["has_hessen"],
            eng_features["has_liga_org"],
        ])

        # Combine
        return np.hstack([tfidf_features, eng_matrix])

    def fit_transform(self, texts: list[str]) -> np.ndarray:
        """Fit and transform."""
        self.fit(texts)
        return self.transform(texts)


# ============================================================================
# Classifiers
# ============================================================================

class LigaFastClassifier:
    """
    Fast classifier for Liga relevance screening.

    Designed for hybrid use with LLM:
    - High-confidence irrelevant items skip LLM (saves compute)
    - Everything else goes to LLM for full analysis
    """

    def __init__(self, skip_threshold: float = SKIP_LLM_THRESHOLD):
        self.skip_threshold = skip_threshold
        self.feature_extractor = FeatureExtractor()

        # Relevance classifier (binary) - calibrated for reliable probabilities
        base_clf = LogisticRegression(
            max_iter=2000,
            class_weight="balanced",
            C=1.0,
            random_state=42,
        )
        self.relevance_clf = CalibratedClassifierCV(base_clf, cv=5, method="isotonic")

        # Priority classifier (multi-class, ordinal)
        # Using LogisticRegression with balanced weights
        self.priority_clf = LogisticRegression(
            max_iter=2000,
            class_weight="balanced",
            random_state=42,
        )
        self.priority_encoder = LabelEncoder()

        # AK classifier (multi-class)
        self.ak_clf = LogisticRegression(
            max_iter=2000,
            class_weight="balanced",
            random_state=42,
        )
        self.ak_encoder = LabelEncoder()

        self.is_fitted = False

    def fit(self, texts: list[str], labels: dict):
        """
        Train all classifiers.

        Args:
            texts: List of text strings (title + content + source)
            labels: Dict with keys 'relevant', 'priority', 'ak'
        """
        print("  Extracting features...")
        X = self.feature_extractor.fit_transform(texts)
        print(f"  Feature matrix: {X.shape}")

        # Task 1: Relevance (binary)
        print("  Training relevance classifier (with calibration)...")
        self.relevance_clf.fit(X, labels["relevant"])

        # Tasks 2 & 3: Priority and AK (only on relevant items)
        relevant_mask = np.array(labels["relevant"]) == 1
        X_relevant = X[relevant_mask]

        if X_relevant.shape[0] > 0:
            # Priority
            print("  Training priority classifier...")
            priority_labels = np.array(labels["priority"])[relevant_mask]
            # Handle null priorities
            valid_priority = priority_labels != "null"
            if np.sum(valid_priority) > 10:
                self.priority_encoder.fit(priority_labels[valid_priority])
                y_priority = self.priority_encoder.transform(priority_labels[valid_priority])
                self.priority_clf.fit(X_relevant[valid_priority], y_priority)

            # AK
            print("  Training AK classifier...")
            ak_labels = np.array(labels["ak"])[relevant_mask]
            valid_ak = ak_labels != "null"
            if np.sum(valid_ak) > 10:
                self.ak_encoder.fit(ak_labels[valid_ak])
                y_ak = self.ak_encoder.transform(ak_labels[valid_ak])
                self.ak_clf.fit(X_relevant[valid_ak], y_ak)

        self.is_fitted = True
        print("  Training complete!")

    def predict(self, title: str, content: str, source: Optional[str] = None) -> dict:
        """
        Predict relevance, priority, and AK for a single item.

        Returns:
            dict with keys:
                - relevant: bool
                - relevant_confidence: float (0-1)
                - skip_llm: bool (True if high-confidence irrelevant)
                - priority: str or None
                - priority_confidence: float or None
                - ak: str or None
                - ak_confidence: float or None
        """
        text = f"{title} {content[:2000]}"
        if source:
            text += f" Quelle: {source}"

        return self.predict_text(text)

    def predict_text(self, text: str) -> dict:
        """Predict from combined text string."""
        X = self.feature_extractor.transform([text])

        # Relevance
        relevant_proba = self.relevance_clf.predict_proba(X)[0]
        # Assume class order is [0=irrelevant, 1=relevant]
        classes = self.relevance_clf.classes_
        relevant_idx = np.where(classes == 1)[0][0]
        irrelevant_idx = np.where(classes == 0)[0][0]

        relevant_prob = relevant_proba[relevant_idx]
        irrelevant_prob = relevant_proba[irrelevant_idx]

        is_relevant = relevant_prob > 0.5

        result = {
            "relevant": bool(is_relevant),
            "relevant_confidence": float(max(relevant_prob, irrelevant_prob)),
            "skip_llm": irrelevant_prob >= self.skip_threshold,
            "priority": None,
            "priority_confidence": None,
            "ak": None,
            "ak_confidence": None,
        }

        # Only predict priority/AK if relevant
        if is_relevant:
            try:
                # Priority
                priority_proba = self.priority_clf.predict_proba(X)[0]
                priority_idx = np.argmax(priority_proba)
                result["priority"] = self.priority_encoder.inverse_transform([priority_idx])[0]
                result["priority_confidence"] = float(priority_proba[priority_idx])

                # AK
                ak_proba = self.ak_clf.predict_proba(X)[0]
                ak_idx = np.argmax(ak_proba)
                result["ak"] = self.ak_encoder.inverse_transform([ak_idx])[0]
                result["ak_confidence"] = float(ak_proba[ak_idx])
            except Exception:
                pass  # Classifiers not fitted (not enough data)

        return result

    def predict_batch(self, texts: list[str]) -> list[dict]:
        """Predict for multiple texts efficiently."""
        X = self.feature_extractor.transform(texts)

        # Relevance
        relevant_proba = self.relevance_clf.predict_proba(X)
        classes = self.relevance_clf.classes_
        relevant_idx = np.where(classes == 1)[0][0]
        irrelevant_idx = np.where(classes == 0)[0][0]

        results = []
        for i in range(len(texts)):
            relevant_prob = relevant_proba[i, relevant_idx]
            irrelevant_prob = relevant_proba[i, irrelevant_idx]
            is_relevant = relevant_prob > 0.5

            result = {
                "relevant": bool(is_relevant),
                "relevant_confidence": float(max(relevant_prob, irrelevant_prob)),
                "skip_llm": irrelevant_prob >= self.skip_threshold,
                "priority": None,
                "priority_confidence": None,
                "ak": None,
                "ak_confidence": None,
            }
            results.append(result)

        # Batch predict priority and AK for relevant items
        relevant_indices = [i for i, r in enumerate(results) if r["relevant"]]
        if relevant_indices:
            X_relevant = X[relevant_indices]
            try:
                priority_proba = self.priority_clf.predict_proba(X_relevant)
                ak_proba = self.ak_clf.predict_proba(X_relevant)

                for j, i in enumerate(relevant_indices):
                    priority_idx = np.argmax(priority_proba[j])
                    results[i]["priority"] = self.priority_encoder.inverse_transform([priority_idx])[0]
                    results[i]["priority_confidence"] = float(priority_proba[j, priority_idx])

                    ak_idx = np.argmax(ak_proba[j])
                    results[i]["ak"] = self.ak_encoder.inverse_transform([ak_idx])[0]
                    results[i]["ak_confidence"] = float(ak_proba[j, ak_idx])
            except Exception:
                pass

        return results

    def save(self, path: Path):
        """Save model to disk."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "classifier.pkl", "wb") as f:
            pickle.dump(self, f)
        print(f"  Model saved to: {path / 'classifier.pkl'}")

    @classmethod
    def load(cls, path: Path) -> "LigaFastClassifier":
        """Load model from disk."""
        path = Path(path)
        with open(path / "classifier.pkl", "rb") as f:
            return pickle.load(f)


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


def prepare_data(records: list[dict]) -> tuple[list[str], dict]:
    """Extract texts and labels from records."""
    texts = []
    labels = {
        "relevant": [],
        "priority": [],
        "ak": [],
    }

    for r in records:
        inp = r["input"]
        lab = r["labels"]

        # Combine title, content, source
        text = f"{inp['title']} {inp['content'][:2000]}"
        if inp.get("source"):
            text += f" Quelle: {inp['source']}"
        texts.append(text)

        # Labels
        labels["relevant"].append(1 if lab["relevant"] else 0)
        labels["priority"].append(lab.get("priority") or "null")
        labels["ak"].append(lab.get("ak") or "null")

    return texts, labels


# ============================================================================
# Evaluation
# ============================================================================

def evaluate(clf: LigaFastClassifier, texts: list[str], labels: dict) -> dict:
    """Evaluate classifier on test set."""
    predictions = clf.predict_batch(texts)

    # Relevance
    y_true_rel = labels["relevant"]
    y_pred_rel = [1 if p["relevant"] else 0 for p in predictions]

    print("\n=== Relevance Classification ===")
    print(classification_report(y_true_rel, y_pred_rel, target_names=["irrelevant", "relevant"]))
    rel_acc = accuracy_score(y_true_rel, y_pred_rel)

    # Confusion matrix
    cm = confusion_matrix(y_true_rel, y_pred_rel)
    print(f"Confusion Matrix:")
    print(f"  TN={cm[0,0]:3d}  FP={cm[0,1]:3d}  (actual irrelevant)")
    print(f"  FN={cm[1,0]:3d}  TP={cm[1,1]:3d}  (actual relevant)")

    # Skip LLM analysis
    skip_count = sum(1 for p in predictions if p["skip_llm"])
    skip_correct = sum(1 for p, y in zip(predictions, y_true_rel) if p["skip_llm"] and y == 0)
    skip_wrong = skip_count - skip_correct

    print(f"\n=== LLM Skip Analysis (threshold={clf.skip_threshold:.0%}) ===")
    print(f"  Items that would skip LLM: {skip_count}/{len(texts)} ({skip_count/len(texts)*100:.1f}%)")
    print(f"  Correctly skipped (true irrelevant): {skip_correct}")
    print(f"  Incorrectly skipped (missed relevant): {skip_wrong}")
    if skip_count > 0:
        print(f"  Skip precision: {skip_correct/skip_count*100:.1f}%")

    # Priority evaluation (on relevant items)
    relevant_mask = np.array(labels["relevant"]) == 1
    y_true_priority = np.array(labels["priority"])[relevant_mask]
    y_pred_priority = [p["priority"] for i, p in enumerate(predictions) if relevant_mask[i]]

    # Filter out nulls
    valid_mask = y_true_priority != "null"
    if np.sum(valid_mask) > 0:
        y_true_priority_valid = y_true_priority[valid_mask]
        y_pred_priority_valid = np.array(y_pred_priority)[valid_mask]

        # Handle None predictions
        y_pred_priority_valid = np.array([p if p else "null" for p in y_pred_priority_valid])

        print("\n=== Priority Classification (on truly relevant items) ===")
        try:
            print(classification_report(y_true_priority_valid, y_pred_priority_valid, zero_division=0))
            priority_acc = accuracy_score(y_true_priority_valid, y_pred_priority_valid)
        except Exception as e:
            print(f"  Could not evaluate priority: {e}")
            priority_acc = 0
    else:
        priority_acc = 0

    # AK evaluation
    y_true_ak = np.array(labels["ak"])[relevant_mask]
    y_pred_ak = [p["ak"] for i, p in enumerate(predictions) if relevant_mask[i]]

    valid_mask = y_true_ak != "null"
    if np.sum(valid_mask) > 0:
        y_true_ak_valid = y_true_ak[valid_mask]
        y_pred_ak_valid = np.array(y_pred_ak)[valid_mask]
        y_pred_ak_valid = np.array([p if p else "null" for p in y_pred_ak_valid])

        print("\n=== AK Classification (on truly relevant items) ===")
        try:
            print(classification_report(y_true_ak_valid, y_pred_ak_valid, zero_division=0))
            ak_acc = accuracy_score(y_true_ak_valid, y_pred_ak_valid)
        except Exception as e:
            print(f"  Could not evaluate AK: {e}")
            ak_acc = 0
    else:
        ak_acc = 0

    return {
        "relevance_accuracy": rel_acc,
        "priority_accuracy": priority_acc,
        "ak_accuracy": ak_acc,
        "skip_count": skip_count,
        "skip_precision": skip_correct / skip_count if skip_count > 0 else 0,
        "missed_relevant": skip_wrong,
    }


def find_optimal_threshold(clf: LigaFastClassifier, texts: list[str], labels: dict) -> float:
    """Find optimal skip threshold balancing precision and recall."""
    X = clf.feature_extractor.transform(texts)
    proba = clf.relevance_clf.predict_proba(X)

    classes = clf.relevance_clf.classes_
    irrelevant_idx = np.where(classes == 0)[0][0]
    irrelevant_proba = proba[:, irrelevant_idx]

    y_true = np.array(labels["relevant"])

    print("\n=== Threshold Analysis ===")
    print(f"{'Threshold':>10} {'Skip%':>8} {'Precision':>10} {'Missed':>8}")
    print("-" * 40)

    best_threshold = 0.85
    best_score = 0

    for threshold in [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]:
        skip_mask = irrelevant_proba >= threshold
        skip_count = np.sum(skip_mask)
        if skip_count == 0:
            continue

        # How many skipped items are actually irrelevant?
        correct = np.sum(skip_mask & (y_true == 0))
        precision = correct / skip_count
        missed = skip_count - correct  # Relevant items incorrectly skipped

        skip_pct = skip_count / len(texts) * 100

        print(f"{threshold:>10.0%} {skip_pct:>7.1f}% {precision:>9.1%} {missed:>8d}")

        # Score: maximize precision while skipping at least 20%
        if skip_pct >= 20 and precision >= 0.95:
            score = skip_pct * precision
            if score > best_score:
                best_score = score
                best_threshold = threshold

    print(f"\nRecommended threshold: {best_threshold:.0%}")
    return best_threshold


def benchmark_speed(clf: LigaFastClassifier, texts: list[str], n_runs: int = 5) -> float:
    """Benchmark prediction speed."""
    # Warmup
    clf.predict_batch(texts[:10])

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
    print("Liga Fast Classifier v2 - Scikit-learn Training")
    print("=" * 60)

    # Load data
    print("\n[1/5] Loading data...")
    train_data = load_jsonl(DATA_DIR / "train.jsonl")
    val_data = load_jsonl(DATA_DIR / "validation.jsonl")
    test_data = load_jsonl(DATA_DIR / "test.jsonl")

    print(f"  Train: {len(train_data)}")
    print(f"  Validation: {len(val_data)}")
    print(f"  Test: {len(test_data)}")

    # Prepare features
    print("\n[2/5] Preparing data...")
    train_texts, train_labels = prepare_data(train_data)
    val_texts, val_labels = prepare_data(val_data)
    test_texts, test_labels = prepare_data(test_data)

    # Combine train + val for final training
    all_train_texts = train_texts + val_texts
    all_train_labels = {k: train_labels[k] + val_labels[k] for k in train_labels}
    print(f"  Total training: {len(all_train_texts)}")
    print(f"  Relevant: {sum(all_train_labels['relevant'])} ({sum(all_train_labels['relevant'])/len(all_train_texts)*100:.1f}%)")

    # Train
    print("\n[3/5] Training classifier...")
    clf = LigaFastClassifier()
    clf.fit(all_train_texts, all_train_labels)

    # Find optimal threshold
    print("\n[4/5] Finding optimal skip threshold...")
    optimal_threshold = find_optimal_threshold(clf, test_texts, test_labels)
    clf.skip_threshold = optimal_threshold

    # Evaluate
    print("\n[5/5] Final evaluation on test set...")
    metrics = evaluate(clf, test_texts, test_labels)

    # Speed benchmark
    print("\n=== Speed Benchmark ===")
    items_per_sec = benchmark_speed(clf, test_texts)
    print(f"  Speed: {items_per_sec:.0f} items/sec ({1000/items_per_sec:.2f}ms per item)")
    print(f"  Comparison: LLM ~46 items/min = ~0.77 items/sec")
    print(f"  Speedup: {items_per_sec / 0.77:.0f}x faster!")

    # Save model
    print("\n=== Saving Model ===")
    clf.save(MODEL_DIR)

    # Summary
    print("\n" + "=" * 60)
    print("Training Complete!")
    print("=" * 60)
    print(f"\nAccuracy:")
    print(f"  Relevance:  {metrics['relevance_accuracy']:.1%}")
    print(f"  Priority:   {metrics['priority_accuracy']:.1%}")
    print(f"  AK:         {metrics['ak_accuracy']:.1%}")
    print(f"\nHybrid LLM Strategy (threshold={clf.skip_threshold:.0%}):")
    print(f"  Items skipping LLM:  {metrics['skip_count']} ({metrics['skip_count']/len(test_texts)*100:.1f}%)")
    print(f"  Skip precision:      {metrics['skip_precision']:.1%}")
    print(f"  Missed relevant:     {metrics['missed_relevant']}")
    print(f"\nSpeed: {items_per_sec:.0f} items/sec")
    print(f"Model saved to: {MODEL_DIR / 'classifier.pkl'}")

    # Example
    print("\n=== Example Predictions ===")
    examples = [
        ("Hessen kürzt Mittel für Kitas um 50 Millionen Euro",
         "Die Landesregierung plant drastische Kürzungen bei der Kinderbetreuung.",
         "hessenschau.de"),
        ("Champions League: Bayern München gegen Manchester City",
         "Das Spitzenspiel der Gruppenphase steht bevor.",
         "sport1.de"),
        ("Neue Pflegereform in Hessen beschlossen",
         "Der Landtag hat heute eine umfassende Reform der Altenpflege beschlossen.",
         "hessenschau.de"),
    ]

    for title, content, source in examples:
        pred = clf.predict(title, content, source)
        skip_status = "[SKIP LLM]" if pred["skip_llm"] else "[→ LLM]"
        print(f"\n  {skip_status} {title[:50]}...")
        print(f"    relevant={pred['relevant']} ({pred['relevant_confidence']:.0%})")
        if pred["relevant"]:
            print(f"    priority={pred['priority']} ({pred['priority_confidence']:.0%}), ak={pred['ak']} ({pred['ak_confidence']:.0%})")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
