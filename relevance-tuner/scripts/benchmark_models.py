#!/usr/bin/env python3
"""
Benchmark comparison: Qwen3 Fine-tuned vs. Scikit-learn

Compares both approaches on the same test set.
"""

import json
import re
import subprocess
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from train_sklearn import LigaClassifier, load_jsonl, prepare_features

# ============================================================================
# Configuration
# ============================================================================

DATA_DIR = Path(__file__).parent.parent / "data" / "final"
MODEL_DIR = Path(__file__).parent.parent / "models" / "sklearn"
QWEN_MODEL = "liga-relevance"
N_SAMPLES = 20  # Number of test samples to compare


def parse_qwen_response(response: str) -> dict:
    """Parse Qwen3 model JSON output."""
    result = {
        "relevant": None,
        "ak": None,
        "priority": None,
    }

    try:
        # Model outputs JSON directly
        data = json.loads(response.strip())
        result["relevant"] = data.get("relevant")
        result["ak"] = data.get("assigned_ak")
        result["priority"] = data.get("priority")
    except json.JSONDecodeError:
        # Fallback: try to extract JSON from response
        import re
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                result["relevant"] = data.get("relevant")
                result["ak"] = data.get("assigned_ak")
                result["priority"] = data.get("priority")
            except json.JSONDecodeError:
                pass

    return result


def query_qwen(text: str) -> tuple[dict, float]:
    """Query Qwen3 model and measure time."""
    start = time.perf_counter()

    result = subprocess.run(
        ["ollama", "run", QWEN_MODEL, text],
        capture_output=True,
        text=True,
        timeout=60,
    )

    elapsed = time.perf_counter() - start
    response = result.stdout.strip()

    return parse_qwen_response(response), elapsed


def format_input(record: dict) -> str:
    """Format record as model input."""
    inp = record["input"]
    return f"""Titel: {inp["title"]}
Inhalt: {inp["content"][:1500]}
Quelle: {inp["source"]}
Datum: {inp["date"]}"""


def main():
    print("=" * 70)
    print("Model Comparison: Qwen3 Fine-tuned vs. Scikit-learn")
    print("=" * 70)

    # Load test data
    print("\n[1/3] Loading test data...")
    test_data = load_jsonl(DATA_DIR / "test.jsonl")
    print(f"  Total test items: {len(test_data)}")
    print(f"  Sampling {N_SAMPLES} items for comparison")

    # Sample a mix of relevant and irrelevant
    import random
    random.seed(42)

    relevant = [r for r in test_data if r["labels"]["relevant"]]
    irrelevant = [r for r in test_data if not r["labels"]["relevant"]]

    sample = random.sample(relevant, min(N_SAMPLES // 2, len(relevant)))
    sample += random.sample(irrelevant, min(N_SAMPLES // 2, len(irrelevant)))
    random.shuffle(sample)

    # Load sklearn model
    print("\n[2/3] Loading models...")
    sklearn_clf = LigaClassifier.load(MODEL_DIR)
    print("  Scikit-learn: Loaded")
    print(f"  Qwen3: {QWEN_MODEL}")

    # Run comparison
    print(f"\n[3/3] Running comparison on {len(sample)} items...")
    print("-" * 70)

    sklearn_times = []
    qwen_times = []
    sklearn_correct = {"relevant": 0, "ak": 0, "priority": 0}
    qwen_correct = {"relevant": 0, "ak": 0, "priority": 0}

    for i, record in enumerate(sample):
        inp = record["input"]
        labels = record["labels"]
        text = format_input(record)

        # Sklearn prediction
        sklearn_text = f"{inp['title']} {inp['content'][:1500]} Quelle: {inp['source']}"
        start = time.perf_counter()
        sklearn_pred = sklearn_clf.predict(sklearn_text)
        sklearn_time = time.perf_counter() - start
        sklearn_times.append(sklearn_time)

        # Qwen prediction
        qwen_pred, qwen_time = query_qwen(text)
        qwen_times.append(qwen_time)

        # Check correctness
        true_rel = labels["relevant"]
        true_ak = labels.get("ak")
        true_priority = labels.get("priority")

        sklearn_rel_correct = sklearn_pred["relevant"] == true_rel
        qwen_rel_correct = qwen_pred["relevant"] == true_rel

        if sklearn_rel_correct:
            sklearn_correct["relevant"] += 1
        if qwen_rel_correct:
            qwen_correct["relevant"] += 1

        if true_rel:  # Only check AK/priority for relevant items
            if sklearn_pred["ak"] == true_ak:
                sklearn_correct["ak"] += 1
            if qwen_pred["ak"] == true_ak:
                qwen_correct["ak"] += 1

            if sklearn_pred["priority"] == true_priority:
                sklearn_correct["priority"] += 1
            if qwen_pred["priority"] == true_priority:
                qwen_correct["priority"] += 1

        # Print comparison
        print(f"\n[{i+1}/{len(sample)}] {inp['title'][:60]}...")
        print(f"  Truth:   rel={true_rel}, ak={true_ak}, priority={true_priority}")
        print(f"  Sklearn: rel={sklearn_pred['relevant']} "
              f"({sklearn_pred['relevant_confidence']:.0%}), "
              f"ak={sklearn_pred['ak']}, priority={sklearn_pred['priority']} "
              f"[{sklearn_time*1000:.1f}ms]")
        print(f"  Qwen3:   rel={qwen_pred['relevant']}, "
              f"ak={qwen_pred['ak']}, priority={qwen_pred['priority']} "
              f"[{qwen_time*1000:.0f}ms]")

        status_sklearn = "correct" if sklearn_rel_correct else "WRONG"
        status_qwen = "correct" if qwen_rel_correct else "WRONG"
        print(f"  Result:  Sklearn={status_sklearn}, Qwen3={status_qwen}")

    # Summary
    n_relevant = sum(1 for r in sample if r["labels"]["relevant"])
    n_total = len(sample)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print("\n=== Accuracy ===")
    print(f"  Relevance ({n_total} items):")
    print(f"    Sklearn: {sklearn_correct['relevant']}/{n_total} = "
          f"{sklearn_correct['relevant']/n_total:.1%}")
    print(f"    Qwen3:   {qwen_correct['relevant']}/{n_total} = "
          f"{qwen_correct['relevant']/n_total:.1%}")

    print(f"\n  AK ({n_relevant} relevant items):")
    print(f"    Sklearn: {sklearn_correct['ak']}/{n_relevant} = "
          f"{sklearn_correct['ak']/n_relevant:.1%}")
    print(f"    Qwen3:   {qwen_correct['ak']}/{n_relevant} = "
          f"{qwen_correct['ak']/n_relevant:.1%}")

    print(f"\n  Priority ({n_relevant} relevant items):")
    print(f"    Sklearn: {sklearn_correct['priority']}/{n_relevant} = "
          f"{sklearn_correct['priority']/n_relevant:.1%}")
    print(f"    Qwen3:   {qwen_correct['priority']}/{n_relevant} = "
          f"{qwen_correct['priority']/n_relevant:.1%}")

    print("\n=== Speed ===")
    sklearn_avg = sum(sklearn_times) / len(sklearn_times) * 1000
    qwen_avg = sum(qwen_times) / len(qwen_times) * 1000
    print(f"  Sklearn: {sklearn_avg:.2f}ms avg per item")
    print(f"  Qwen3:   {qwen_avg:.0f}ms avg per item")
    print(f"  Speedup: {qwen_avg/sklearn_avg:.0f}x")

    print("\n=== Recommendation ===")
    if qwen_correct["relevant"] > sklearn_correct["relevant"] + 2:
        print("  Qwen3 is significantly more accurate. Use Qwen3 for critical decisions.")
    elif sklearn_correct["relevant"] >= qwen_correct["relevant"] - 1:
        print("  Scikit-learn is comparable in accuracy but much faster.")
        print("  Consider: Hybrid approach (sklearn first, qwen3 for low-confidence)")
    else:
        print("  Both have similar accuracy. Choose based on speed requirements.")

    print("=" * 70)


if __name__ == "__main__":
    main()
