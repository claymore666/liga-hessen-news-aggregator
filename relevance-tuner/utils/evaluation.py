#!/usr/bin/env python3
"""
Standard evaluation utilities for Liga Hessen classifiers.

Provides consistent metrics across all classifier types.
"""

import sys
from collections import Counter
from typing import Any, Optional

# Add parent directory to path for config import
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from config import AK_CLASSES, PRIORITY_LEVELS


def evaluate_relevance(
    y_true: list[int],
    y_pred: list[int],
    print_report: bool = True,
) -> dict:
    """
    Evaluate binary relevance classification.

    Args:
        y_true: True labels (0=irrelevant, 1=relevant)
        y_pred: Predicted labels
        print_report: Whether to print detailed report

    Returns:
        Dict with accuracy, precision, recall, f1
    """
    from sklearn.metrics import (
        accuracy_score,
        classification_report,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
    )

    accuracy = accuracy_score(y_true, y_pred)
    precision = precision_score(y_true, y_pred, zero_division=0)
    recall = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)

    if print_report:
        print("\n=== Relevance Classification ===")
        print(f"Accuracy:  {accuracy:.1%}")
        print(f"Precision: {precision:.1%}")
        print(f"Recall:    {recall:.1%}")
        print(f"F1:        {f1:.1%}")
        print("\nConfusion Matrix:")
        cm = confusion_matrix(y_true, y_pred)
        print(f"  Predicted:  0      1")
        print(f"  Actual 0: {cm[0][0]:4d}   {cm[0][1]:4d}")
        print(f"  Actual 1: {cm[1][0]:4d}   {cm[1][1]:4d}")

    return {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def evaluate_priority(
    y_true: list[str],
    y_pred: list[str],
    print_report: bool = True,
) -> dict:
    """
    Evaluate priority classification (4-class).

    Args:
        y_true: True priority labels
        y_pred: Predicted priority labels
        print_report: Whether to print detailed report

    Returns:
        Dict with accuracy, f1_macro, within_one_accuracy
    """
    from sklearn.metrics import accuracy_score, classification_report, f1_score

    # Filter out empty labels
    pairs = [(t, p) for t, p in zip(y_true, y_pred) if t and p]
    if not pairs:
        return {"accuracy": 0.0, "f1_macro": 0.0, "within_one": 0.0}

    y_true_f, y_pred_f = zip(*pairs)

    accuracy = accuracy_score(y_true_f, y_pred_f)
    f1_macro = f1_score(y_true_f, y_pred_f, average="macro", zero_division=0)

    # Within-one accuracy (adjacent priority level counts as correct)
    priority_order = {p: i for i, p in enumerate(PRIORITY_LEVELS)}
    within_one = 0
    for true, pred in zip(y_true_f, y_pred_f):
        if true in priority_order and pred in priority_order:
            if abs(priority_order[true] - priority_order[pred]) <= 1:
                within_one += 1
    within_one_acc = within_one / len(y_true_f) if y_true_f else 0

    if print_report:
        print("\n=== Priority Classification ===")
        print(f"Accuracy:         {accuracy:.1%}")
        print(f"F1 (macro):       {f1_macro:.1%}")
        print(f"Within-1 level:   {within_one_acc:.1%}")
        print("\nClassification Report:")
        print(classification_report(y_true_f, y_pred_f, zero_division=0))

    return {
        "accuracy": accuracy,
        "f1_macro": f1_macro,
        "within_one": within_one_acc,
    }


def evaluate_ak(
    y_true: list[str],
    y_pred: list[str],
    print_report: bool = True,
) -> dict:
    """
    Evaluate AK classification (6-class).

    Args:
        y_true: True AK labels
        y_pred: Predicted AK labels
        print_report: Whether to print detailed report

    Returns:
        Dict with accuracy, f1_macro, per_class_accuracy
    """
    from sklearn.metrics import (
        accuracy_score,
        classification_report,
        confusion_matrix,
        f1_score,
    )

    # Filter out empty labels
    pairs = [(t, p) for t, p in zip(y_true, y_pred) if t and p]
    if not pairs:
        return {"accuracy": 0.0, "f1_macro": 0.0, "per_class": {}}

    y_true_f, y_pred_f = zip(*pairs)

    accuracy = accuracy_score(y_true_f, y_pred_f)
    f1_macro = f1_score(y_true_f, y_pred_f, average="macro", zero_division=0)

    # Per-class accuracy
    per_class = {}
    true_counts = Counter(y_true_f)
    for ak in AK_CLASSES:
        if true_counts[ak] > 0:
            correct = sum(1 for t, p in zip(y_true_f, y_pred_f) if t == ak and p == ak)
            per_class[ak] = correct / true_counts[ak]
        else:
            per_class[ak] = 0.0

    if print_report:
        print("\n=== AK Classification ===")
        print(f"Accuracy:    {accuracy:.1%}")
        print(f"F1 (macro):  {f1_macro:.1%}")
        print("\nPer-class accuracy:")
        for ak in AK_CLASSES:
            count = true_counts[ak]
            acc = per_class[ak]
            print(f"  {ak}: {acc:.1%} ({count} samples)")
        print("\nClassification Report:")
        print(classification_report(y_true_f, y_pred_f, zero_division=0))

    return {
        "accuracy": accuracy,
        "f1_macro": f1_macro,
        "per_class": per_class,
    }


def evaluate_hierarchical(
    y_true_rel: list[int],
    y_pred_rel: list[int],
    y_true_pri: list[str],
    y_pred_pri: list[str],
    y_true_ak: list[str],
    y_pred_ak: list[str],
    print_report: bool = True,
) -> dict:
    """
    Evaluate full hierarchical classification.

    Only evaluates priority/AK for items that are truly relevant.
    """
    results = {}

    # Relevance
    results["relevance"] = evaluate_relevance(
        y_true_rel, y_pred_rel, print_report=print_report
    )

    # Priority (only for truly relevant items)
    rel_true_pri = [p for p, r in zip(y_true_pri, y_true_rel) if r == 1]
    rel_pred_pri = [p for p, r in zip(y_pred_pri, y_true_rel) if r == 1]
    results["priority"] = evaluate_priority(
        rel_true_pri, rel_pred_pri, print_report=print_report
    )

    # AK (only for truly relevant items)
    rel_true_ak = [a for a, r in zip(y_true_ak, y_true_rel) if r == 1]
    rel_pred_ak = [a for a, r in zip(y_pred_ak, y_true_rel) if r == 1]
    results["ak"] = evaluate_ak(rel_true_ak, rel_pred_ak, print_report=print_report)

    if print_report:
        print("\n" + "=" * 50)
        print("SUMMARY")
        print("=" * 50)
        print(f"Relevance accuracy: {results['relevance']['accuracy']:.1%}")
        print(f"Priority accuracy:  {results['priority']['accuracy']:.1%}")
        print(f"AK accuracy:        {results['ak']['accuracy']:.1%}")

    return results


if __name__ == "__main__":
    # Quick test with dummy data
    print("Testing evaluation utilities...")

    # Relevance test
    y_true_rel = [1, 1, 0, 0, 1, 0, 1, 1, 0, 0]
    y_pred_rel = [1, 1, 0, 1, 1, 0, 0, 1, 0, 0]
    evaluate_relevance(y_true_rel, y_pred_rel)

    # Priority test
    y_true_pri = ["low", "medium", "high", "critical", "medium"]
    y_pred_pri = ["low", "medium", "medium", "high", "high"]
    evaluate_priority(y_true_pri, y_pred_pri)

    # AK test
    y_true_ak = ["AK1", "AK2", "AK3", "AK5", "QAG"]
    y_pred_ak = ["AK1", "AK2", "AK4", "AK5", "QAG"]
    evaluate_ak(y_true_ak, y_pred_ak)

    print("\nAll tests passed!")
