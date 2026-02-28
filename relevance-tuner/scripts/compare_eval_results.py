#!/usr/bin/env python3
"""Compare evaluation results across runs.

Reads all result files from evaluations/results/ and prints a comparison table.

Usage:
    python scripts/compare_eval_results.py                    # all results
    python scripts/compare_eval_results.py --type llm         # only LLM
    python scripts/compare_eval_results.py --type classifier   # only classifier
    python scripts/compare_eval_results.py --type topic        # only topic
    python scripts/compare_eval_results.py --latest 5         # last 5 runs
"""

import argparse
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).parent.parent / "evaluations" / "results"


def load_results(results_dir: Path, type_filter: str | None = None) -> list[dict]:
    """Load all result files, optionally filtered by type."""
    results = []

    if not results_dir.exists():
        return results

    for f in sorted(results_dir.glob("*.json")):
        if f.name.startswith("."):
            continue
        try:
            data = json.loads(f.read_text())
            data["_file"] = f.name
            result_type = data.get("type", "unknown")
            if type_filter and result_type != type_filter:
                continue
            results.append(data)
        except (json.JSONDecodeError, KeyError):
            continue

    # Sort by timestamp
    results.sort(key=lambda x: x.get("timestamp", ""))
    return results


def print_comparison(results: list[dict]):
    """Print comparison table."""
    if not results:
        print("No evaluation results found.")
        print(f"  Results directory: {RESULTS_DIR}")
        print("  Run run_llm_eval.py, run_classifier_eval.py, or run_topic_eval.py first.")
        return

    # Split topic results from relevance results
    topic_results = [r for r in results if r.get("type") == "topic"]
    relevance_results = [r for r in results if r.get("type") != "topic"]

    if relevance_results:
        _print_relevance_comparison(relevance_results)

    if topic_results:
        _print_topic_comparison(topic_results)


def _print_relevance_comparison(results: list[dict]):
    """Print comparison table for relevance evals (llm/classifier)."""
    eval_ver = results[0].get("eval_set_version", "?")
    total_items = results[0].get("metrics", {}).get("total_items", "?")

    print("=" * 120)
    print(f"RELEVANCE EVAL RESULTS (eval_set v{eval_ver}, {total_items} items)")
    print("=" * 120)

    header = f"{'Type':<12} {'Label':<25} {'Date':<12} {'Acc':>6} {'Prec':>6} {'Rec':>6} {'F1':>6} {'Pri':>6} {'P±1':>6} {'AK':>6}"
    print(header)
    print("-" * 120)

    for r in results:
        m = r.get("metrics", {})
        rtype = r.get("type", "?")
        date = r.get("timestamp", "")[:10]

        # Build label
        if rtype == "llm":
            label = f"{r.get('prompt_tag', '?')}/{r.get('model', '?')[:15]}"
        else:
            label = r.get("label", r.get("model_fingerprint", "?"))

        label = label[:25]

        acc = m.get("accuracy", 0)
        prec = m.get("precision", 0)
        rec = m.get("recall", 0)
        f1 = m.get("f1", 0)
        pri = m.get("priority_accuracy", 0)
        pri_w1 = m.get("priority_within_one", 0)
        ak = m.get("ak_exact_accuracy", m.get("ak_overlap_accuracy", 0))

        print(f"{rtype:<12} {label:<25} {date:<12} {acc:5.0%} {prec:5.0%} {rec:5.0%} {f1:5.0%} {pri:5.0%} {pri_w1:5.0%} {ak:5.0%}")

    print("=" * 120)

    # Show delta between last two results of same type
    for rtype in ["llm", "classifier"]:
        typed = [r for r in results if r.get("type") == rtype]
        if len(typed) >= 2:
            prev = typed[-2].get("metrics", {})
            curr = typed[-1].get("metrics", {})

            def delta(key):
                d = curr.get(key, 0) - prev.get(key, 0)
                return f"+{d:.0%}" if d >= 0 else f"{d:.0%}"

            ak_key = "ak_exact_accuracy" if "ak_exact_accuracy" in curr else "ak_overlap_accuracy"
            print(
                f"Delta (last two {rtype}):{'':>13} {'':12} "
                f"{delta('accuracy'):>6} {delta('precision'):>6} {delta('recall'):>6} "
                f"{delta('f1'):>6} {delta('priority_accuracy'):>6} "
                f"{delta('priority_within_one'):>6} {delta(ak_key):>6}"
            )

    # Show FP/FN breakdown for latest of each type
    for rtype in ["llm", "classifier"]:
        typed = [r for r in results if r.get("type") == rtype]
        if typed:
            latest = typed[-1]
            m = latest.get("metrics", {})
            fp_sub = m.get("fp_by_subcategory", {})
            fn_sub = m.get("fn_by_subcategory", {})
            if fp_sub or fn_sub:
                label = latest.get("prompt_tag", latest.get("label", "?"))
                print(f"\n  Error breakdown ({rtype} / {label}):")
                if fp_sub:
                    print(f"    FP: {', '.join(f'{k}={v}' for k, v in sorted(fp_sub.items(), key=lambda x: -x[1]))}")
                if fn_sub:
                    print(f"    FN: {', '.join(f'{k}={v}' for k, v in sorted(fn_sub.items(), key=lambda x: -x[1]))}")

    print()


def _print_topic_comparison(results: list[dict]):
    """Print comparison table for topic evals."""
    eval_ver = results[0].get("eval_set_version", "?")
    total_items = results[0].get("metrics", {}).get("total_items", "?")

    print("=" * 100)
    print(f"TOPIC EVAL RESULTS (eval_set v{eval_ver}, {total_items} items)")
    print("=" * 100)

    header = f"{'Label':<30} {'Mode':<6} {'Date':<12} {'Acc':>6} {'Sonst':>6} {'Parse':>6} {'Topics':>6} {'Time':>6}"
    print(header)
    print("-" * 100)

    for r in results:
        m = r.get("metrics", {})
        date = r.get("timestamp", "")[:10]
        label = f"{r.get('prompt_tag', '?')}/{r.get('model', '?')[:15]}"[:30]
        mode = r.get("mode", "?")[:6]

        acc = m.get("accuracy", 0)
        sonst = m.get("sonstiges_rate", 0)
        parse = m.get("parse_success_rate", 0)
        topics = m.get("unique_topics_predicted", 0)
        avg_time = m.get("avg_time_s", 0)

        print(f"{label:<30} {mode:<6} {date:<12} {acc:5.0%} {sonst:5.0%} {parse:5.0%} {topics:>6} {avg_time:5.1f}s")

    print("=" * 100)

    # Delta between last two topic runs
    if len(results) >= 2:
        prev = results[-2].get("metrics", {})
        curr = results[-1].get("metrics", {})

        def delta(key):
            d = curr.get(key, 0) - prev.get(key, 0)
            return f"+{d:.0%}" if d >= 0 else f"{d:.0%}"

        print(
            f"Delta (last two topic):{'':>15} {'':6} {'':12} "
            f"{delta('accuracy'):>6} {delta('sonstiges_rate'):>6} "
            f"{delta('parse_success_rate'):>6}"
        )

    # Show top confusions for latest run
    latest = results[-1]
    m = latest.get("metrics", {})
    confusions = m.get("top_confusions", [])
    if confusions:
        label = latest.get("prompt_tag", "?")
        print(f"\n  Top confusions (topic / {label}):")
        for conf in confusions[:8]:
            print(f"    {conf['expected']:30s} → {conf['got']:30s}  ({conf['count']}x)")

    print()


def main():
    parser = argparse.ArgumentParser(description="Compare eval results across runs")
    parser.add_argument("--type", choices=["llm", "classifier", "topic"], default=None,
                        help="Filter by result type")
    parser.add_argument("--latest", type=int, default=None,
                        help="Show only the latest N results")
    parser.add_argument("--results-dir", type=str, default=None,
                        help=f"Results directory (default: {RESULTS_DIR})")
    args = parser.parse_args()

    results_dir = Path(args.results_dir) if args.results_dir else RESULTS_DIR
    results = load_results(results_dir, args.type)

    if args.latest and len(results) > args.latest:
        results = results[-args.latest:]

    print_comparison(results)


if __name__ == "__main__":
    main()
