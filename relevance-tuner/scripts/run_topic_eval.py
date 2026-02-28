#!/usr/bin/env python3
"""Run Ollama LLM topic assignment against the fixed topic eval set.

Simulates the production topic extraction flow: sends the article through
the analysis system prompt, then follows up with the topic assignment prompt.
Compares results against Haiku ground truth.

Usage:
    python scripts/run_topic_eval.py                          # defaults
    python scripts/run_topic_eval.py --prompt-tag baseline     # label for run
    python scripts/run_topic_eval.py --model qwen3:32b         # different model
    python scripts/run_topic_eval.py --limit 20                # quick test

Requires:
    - Ollama running locally (or set OLLAMA_URL)
    - evaluations/topic_eval_set.json (run curate_topic_eval_set.py first)
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# Add parent to path for topic_taxonomy import
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "news-aggregator" / "backend" / "services"))

from topic_taxonomy import TOPIC_TAXONOMY, SONSTIGES, validate_topic

# ============================================================================
# Configuration
# ============================================================================

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = "qwen3:14b-q8_0"
EVAL_SET_PATH = Path(__file__).parent.parent / "evaluations" / "topic_eval_set.json"
RESULTS_DIR = Path(__file__).parent.parent / "evaluations" / "results"


# ============================================================================
# Shared functions (from run_llm_eval.py)
# ============================================================================

def get_system_prompt() -> str:
    """Read the production system prompt from processor.py."""
    processor_path = (
        Path(__file__).parent.parent.parent
        / "news-aggregator" / "backend" / "services" / "processor.py"
    )
    if not processor_path.exists():
        print(f"ERROR: Cannot find {processor_path}")
        sys.exit(1)

    content = processor_path.read_text()
    match = re.search(r'ANALYSIS_SYSTEM_PROMPT = """(.*?)"""', content, re.DOTALL)
    if not match:
        print("ERROR: Cannot find ANALYSIS_SYSTEM_PROMPT in processor.py")
        sys.exit(1)

    return match.group(1).strip()


def get_topic_follow_up_prompt() -> str:
    """Build the topic follow-up prompt matching production processor.py.

    IMPORTANT: Keep this in sync with processor.py _classify_topic().
    """
    # Exclude Sozialpolitik from main list — add as last-resort option
    taxonomy_list = "\n".join(
        f"- {t}" for t in TOPIC_TAXONOMY if t != "Sozialpolitik"
    )
    return (
        "Ordne diesen Artikel GENAU EINEM Thema aus der folgenden Liste zu.\n\n"
        f"THEMENLISTE:\n{taxonomy_list}\n\n"
        "REGELN:\n"
        "- W\u00e4hle das Thema, das am besten beschreibt, WARUM der Artikel f\u00fcr die "
        "Wohlfahrtspflege relevant ist \u2014 nicht worum es allgemein geht.\n"
        "- Bei thematischer \u00dcberschneidung w\u00e4hle das ENGERE, SPEZIFISCHERE Thema.\n"
        "- KEINE Parteinamen, Organisationsnamen oder Ortsnamen als Thema.\n\n"
        "UNTERSCHEIDUNGEN:\n"
        "- Tarifpolitik = Tarifvertrag, Warnstreik, Arbeitskampf, Mindestlohn, "
        "Lohnerh\u00f6hung \u2014 auch wenn in Kitas/Krankenh\u00e4usern gestreikt wird\n"
        "- Senioren und Alter = Rente, Altersarmut, Alterssicherung, Rentenreform\n"
        "- Fachkr\u00e4ftemangel = struktureller Personalmangel, Fachkr\u00e4ftel\u00fccke\n"
        "- Pflege = Pflegepersonal, Pflegebeitrag, Pflegereform\n"
        "- B\u00fcrokratieabbau = Entb\u00fcrokratisierung, Regulierungsabbau\n"
        "- Gesundheitsversorgung = Krankenhausreform, Klinikschlie\u00dfung, Krankenkasse\n"
        "- Migration und Flucht = auch Abschiebung, Asylpolitik, Menschenschmuggel\n"
        "- Behinderung und Inklusion = Schwerbehinderung, Behindertenrecht, "
        "Inklusion, Teilhabe \u2014 auch wenn es um Rente/Kindergeld f\u00fcr Behinderte geht\n"
        "- Wohnen und Wohnungsnot = Wohngeld, Hessengeld, Mietpreisbremse, "
        "Sozialwohnungen, Wohnraumf\u00f6rderung\n"
        "- Sozialleistungen = B\u00fcrgergeld, Grundsicherung, Kurzarbeitergeld "
        "\u2014 NICHT Armut allgemein (\u2192 Armut und Existenzsicherung), "
        "NICHT Arbeitsmarktpolitik (\u2192 Arbeitsmarkt), "
        "NICHT Leistungen f\u00fcr Gefl\u00fcchtete (\u2192 Migration und Flucht), "
        "NICHT Behindertenleistungen (\u2192 Behinderung und Inklusion), "
        "NICHT Wohnungsf\u00f6rderung (\u2192 Wohnen und Wohnungsnot)\n\n"
        "BEISPIELE:\n"
        "- \u201eMindestlohn steigt auf 13,90\u20ac\u201c \u2192 Tarifpolitik\n"
        "- \u201eWarnstreiks in Kitas und Unikliniken\u201c \u2192 Tarifpolitik\n"
        "- \u201eSteuerbefreiung f\u00fcr Gewerkschaftsbeitr\u00e4ge\u201c \u2192 Tarifpolitik\n"
        "- \u201e200 Mrd. f\u00fcr Rentenleistungen\u201c \u2192 Senioren und Alter\n"
        "- \u201eAlterssicherungskommission konstituiert\u201c \u2192 Senioren und Alter\n"
        "- \u201ePflegebeitrag stoppen, Strukturreform\u201c \u2192 Pflege\n"
        "- \u201eCDU will B\u00fcrokratieabbau f\u00fcr Unternehmen\u201c \u2192 B\u00fcrokratieabbau\n"
        "- \u201eReform der Grundsicherung f\u00fcr Arbeitsuchende\u201c \u2192 Sozialleistungen\n"
        "- \u201eInflation treibt Lebensmittelpreise hoch\u201c \u2192 Armut und Existenzsicherung\n\n"
        "Wenn KEIN Thema aus der Liste passt, pr\u00fcfe:\n"
        "- Sozialpolitik \u2014 NUR f\u00fcr \u00fcbergreifende Sozialstaats-Debatten "
        "ohne klaren Fachbezug\n"
        "- Sonstiges \u2014 mit Vorschlag\n\n"
        "Antwort NUR als JSON:\n"
        '{"topic": "Thema aus Liste"}\n'
        "oder bei Sonstiges:\n"
        '{"topic": "Sonstiges", "topic_suggestion": "Dein Vorschlag"}'
    )


def call_ollama_chat(model: str, messages: list[dict], temperature: float = 0.2,
                     max_tokens: int = 1024, think: bool = True) -> dict:
    """Call Ollama chat API and return response with timing info.

    Thinking is enabled by default — it significantly improves topic
    classification accuracy (from ~76% to ~86%) by allowing the model
    to reason through disambiguation before responding.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }

    start = time.time()
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=180)
        elapsed = time.time() - start
        data = r.json()

        message = data.get("message", {})
        response_text = message.get("content", "")

        eval_count = data.get("eval_count", 0)
        eval_duration = data.get("eval_duration", 0)
        tok_per_sec = eval_count / (eval_duration / 1e9) if eval_duration > 0 else 0

        return {
            "response": response_text,
            "wall_time_s": round(elapsed, 1),
            "tokens_generated": eval_count,
            "tok_per_sec": round(tok_per_sec, 1),
            "error": None,
        }
    except Exception as e:
        return {
            "response": "",
            "wall_time_s": round(time.time() - start, 1),
            "tokens_generated": 0,
            "tok_per_sec": 0,
            "error": str(e),
        }


def call_ollama_generate(model: str, prompt: str, system: str,
                         think: bool = True) -> dict:
    """Call Ollama generate API for the initial analysis."""
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "think": think,
        "options": {"temperature": 0.3, "num_predict": 4096},
    }

    start = time.time()
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=180)
        elapsed = time.time() - start
        data = r.json()

        response = data.get("response", "")
        if not response and data.get("thinking"):
            response = data["thinking"]

        eval_count = data.get("eval_count", 0)
        eval_duration = data.get("eval_duration", 0)
        tok_per_sec = eval_count / (eval_duration / 1e9) if eval_duration > 0 else 0

        return {
            "response": response,
            "wall_time_s": round(elapsed, 1),
            "tokens_generated": eval_count,
            "tok_per_sec": round(tok_per_sec, 1),
            "error": None,
        }
    except Exception as e:
        return {
            "response": "",
            "wall_time_s": round(time.time() - start, 1),
            "tokens_generated": 0,
            "tok_per_sec": 0,
            "error": str(e),
        }


def parse_topic(response_text: str) -> dict:
    """Extract topic from model response JSON."""
    text = response_text
    if "</think>" in text:
        text = text.split("</think>", 1)[-1]

    # Remove markdown code blocks
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            raw_topic = data.get("topic", "")
            topic, suggestion = validate_topic(raw_topic)
            return {
                "topic": topic,
                "topic_suggestion": suggestion or data.get("topic_suggestion"),
                "parse_ok": True,
            }
        except json.JSONDecodeError:
            pass

    return {"topic": SONSTIGES, "topic_suggestion": None, "parse_ok": False}


# ============================================================================
# Metrics
# ============================================================================

def compute_topic_metrics(results: list[dict]) -> dict:
    """Compute topic evaluation metrics."""
    total = len(results)
    if total == 0:
        return {}

    # Exact match accuracy
    exact_matches = sum(1 for r in results if r["correct_topic"])
    accuracy = exact_matches / total

    # Sonstiges rate (should be low for relevant items)
    sonstiges_count = sum(1 for r in results if r["got_topic"] == SONSTIGES)
    sonstiges_rate = sonstiges_count / total

    # Parse success
    parse_ok = sum(1 for r in results if r["parse_ok"])

    # Per-topic precision and recall
    # For each topic: precision = correct/predicted, recall = correct/actual
    predicted_topics = Counter(r["got_topic"] for r in results)
    actual_topics = Counter(r["expected_topic"] for r in results)
    correct_per_topic = Counter()
    for r in results:
        if r["correct_topic"]:
            correct_per_topic[r["expected_topic"]] += 1

    per_topic = {}
    all_topics = set(predicted_topics.keys()) | set(actual_topics.keys())
    for topic in sorted(all_topics):
        predicted = predicted_topics.get(topic, 0)
        actual = actual_topics.get(topic, 0)
        correct = correct_per_topic.get(topic, 0)
        precision = correct / predicted if predicted > 0 else 0
        recall = correct / actual if actual > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        per_topic[topic] = {
            "predicted": predicted,
            "actual": actual,
            "correct": correct,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    # Confusion matrix (only mismatches, top confusions)
    confusions = Counter()
    for r in results:
        if not r["correct_topic"]:
            confusions[(r["expected_topic"], r["got_topic"])] += 1

    confusion_list = [
        {"expected": exp, "got": got, "count": count}
        for (exp, got), count in confusions.most_common(20)
    ]

    # Timing
    times = [r["wall_time_s"] for r in results if r["wall_time_s"] > 0]
    tps = [r["tok_per_sec"] for r in results if r["tok_per_sec"] > 0]

    return {
        "accuracy": round(accuracy, 4),
        "exact_matches": exact_matches,
        "total_items": total,
        "sonstiges_rate": round(sonstiges_rate, 4),
        "sonstiges_count": sonstiges_count,
        "parse_success_rate": round(parse_ok / total, 4),
        "unique_topics_predicted": len(predicted_topics),
        "unique_topics_actual": len(actual_topics),
        "per_topic": per_topic,
        "top_confusions": confusion_list,
        "avg_time_s": round(sum(times) / len(times), 1) if times else 0,
        "avg_tok_per_sec": round(sum(tps) / len(tps), 1) if tps else 0,
    }


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Run topic eval against fixed topic eval set")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                        help=f"Ollama model (default: {DEFAULT_MODEL})")
    parser.add_argument("--prompt-tag", type=str, default=None,
                        help="Label for this eval run (default: auto from git)")
    parser.add_argument("--eval-set", type=str, default=None,
                        help=f"Path to topic eval set JSON (default: {EVAL_SET_PATH})")
    parser.add_argument("--no-think", action="store_true",
                        help="Disable Qwen3 thinking mode")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only evaluate first N items (for testing)")
    parser.add_argument("--topic-only", action="store_true",
                        help="Skip full analysis, send topic prompt directly (faster but less realistic)")
    args = parser.parse_args()

    eval_path = Path(args.eval_set) if args.eval_set else EVAL_SET_PATH

    # Load eval set
    if not eval_path.exists():
        print(f"ERROR: Topic eval set not found: {eval_path}")
        print("Run curate_topic_eval_set.py first.")
        sys.exit(1)

    eval_data = json.loads(eval_path.read_text())
    items = eval_data["items"]
    eval_version = eval_data.get("version", 1)

    # Filter to items with ground truth
    items = [i for i in items if i.get("ground_truth")]
    if args.limit:
        items = items[:args.limit]

    if not items:
        print("ERROR: No items with ground truth in topic eval set")
        sys.exit(1)

    # Determine tag
    tag = args.prompt_tag
    if not tag:
        try:
            import subprocess
            result = subprocess.run(["git", "describe", "--tags", "--abbrev=0"],
                                    capture_output=True, text=True, timeout=5)
            tag = result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            tag = "unknown"

    model = args.model
    system_prompt = get_system_prompt()
    topic_follow_up = get_topic_follow_up_prompt()

    print("=" * 90)
    print("TOPIC EVALUATION")
    print("=" * 90)
    print(f"  Model: {model}")
    print(f"  Tag: {tag}")
    print(f"  Eval set: {eval_path} (v{eval_version})")
    print(f"  Items: {len(items)} (with ground truth)")
    print(f"  Mode: {'topic-only (fast)' if args.topic_only else 'full pipeline (analysis + topic)'}")
    print(f"  Thinking: {'disabled' if args.no_think else 'enabled'}")
    print(f"  Ollama: {OLLAMA_URL}")

    # Run evaluation
    results = []
    print(f"\n{'#':>3} {'ID':>6} {'Expected':30.30} {'Got':30.30} {'Match':>5} {'Time':>6}")
    print("-" * 90)

    for idx, item in enumerate(items, 1):
        gt = item["ground_truth"]
        expected_topic = gt["topic"]

        content = item.get("content", "")[:2000]
        user_prompt = f"Analysiere diesen Nachrichtenartikel:\n\nTitel: {item['title']}\n\nInhalt: {content}"

        total_time = 0
        total_tokens = 0

        if args.topic_only:
            # Fast mode: send topic prompt directly without full analysis
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": '{"relevant": true, "summary": "..."}'},
                {"role": "user", "content": topic_follow_up},
            ]
            r = call_ollama_chat(model, messages, temperature=0.2, max_tokens=1024,
                                         think=not args.no_think)
            total_time = r["wall_time_s"]
            total_tokens = r["tokens_generated"]
            topic_result = parse_topic(r["response"])
        else:
            # Full pipeline: analysis first, then topic follow-up
            # Step 1: Full analysis
            r1 = call_ollama_generate(model, user_prompt, system_prompt,
                                       think=not args.no_think)
            total_time += r1["wall_time_s"]
            total_tokens += r1["tokens_generated"]

            if r1["error"]:
                topic_result = {"topic": SONSTIGES, "topic_suggestion": None, "parse_ok": False}
            else:
                # Step 2: Topic follow-up via chat API (simulates production flow)
                analysis_response = r1["response"]
                # Clean thinking tags for the assistant message
                clean_response = analysis_response
                if "</think>" in clean_response:
                    clean_response = clean_response.split("</think>", 1)[-1].strip()

                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": clean_response},
                    {"role": "user", "content": topic_follow_up},
                ]
                r2 = call_ollama_chat(model, messages, temperature=0.2, max_tokens=1024,
                                         think=not args.no_think)
                total_time += r2["wall_time_s"]
                total_tokens += r2["tokens_generated"]
                topic_result = parse_topic(r2["response"])

        got_topic = topic_result["topic"]
        correct = (expected_topic == got_topic)

        result = {
            "id": item["id"],
            "title": item["title"][:60],
            "source": item.get("source", ""),
            "priority": item.get("priority", ""),
            "expected_topic": expected_topic,
            "got_topic": got_topic,
            "current_topic": item.get("current_topic", ""),
            "correct_topic": correct,
            "got_suggestion": topic_result.get("topic_suggestion"),
            "parse_ok": topic_result["parse_ok"],
            "wall_time_s": total_time,
            "tok_per_sec": round(total_tokens / total_time, 1) if total_time > 0 else 0,
            "tokens": total_tokens,
            "error": r1["error"] if not args.topic_only else r["error"],
        }
        results.append(result)

        mark = "OK" if correct else "MISS"
        print(f"{idx:>3} {item['id']:>6} {expected_topic:30.30} {got_topic:30.30} {mark:>5} {total_time:5.1f}s")

    # Compute metrics
    metrics = compute_topic_metrics(results)

    # Print summary
    print(f"\n{'=' * 90}")
    print("SUMMARY")
    print(f"{'=' * 90}")
    print(f"  Accuracy:           {metrics['accuracy']:.1%} ({metrics['exact_matches']}/{metrics['total_items']})")
    print(f"  Sonstiges rate:     {metrics['sonstiges_rate']:.1%} ({metrics['sonstiges_count']})")
    print(f"  Parse OK:           {metrics['parse_success_rate']:.0%}")
    print(f"  Topics predicted:   {metrics['unique_topics_predicted']}")
    print(f"  Topics in GT:       {metrics['unique_topics_actual']}")
    print(f"  Avg time:           {metrics['avg_time_s']}s")
    print(f"  Avg tok/s:          {metrics['avg_tok_per_sec']}")

    # Per-topic breakdown (only topics with actual > 0, sorted by count)
    print(f"\n  Per-topic breakdown:")
    print(f"  {'Topic':35s} {'Act':>4} {'Pred':>4} {'Corr':>4} {'Prec':>6} {'Rec':>6} {'F1':>6}")
    print(f"  {'-' * 75}")
    sorted_topics = sorted(
        metrics["per_topic"].items(),
        key=lambda x: x[1]["actual"],
        reverse=True,
    )
    for topic, stats in sorted_topics:
        if stats["actual"] > 0 or stats["predicted"] > 0:
            print(
                f"  {topic:35s} {stats['actual']:>4} {stats['predicted']:>4} "
                f"{stats['correct']:>4} {stats['precision']:5.0%} {stats['recall']:5.0%} "
                f"{stats['f1']:5.0%}"
            )

    # Top confusions
    if metrics["top_confusions"]:
        print(f"\n  Top confusions (expected → got):")
        for conf in metrics["top_confusions"][:10]:
            print(f"    {conf['expected']:30s} → {conf['got']:30s}  ({conf['count']}x)")

    # Show all mismatches
    misses = [r for r in results if not r["correct_topic"]]
    if misses:
        print(f"\n  Mismatches ({len(misses)}):")
        for r in misses:
            print(f"    {r['id']:>6}: exp={r['expected_topic']}, got={r['got_topic']} — {r['title']}")

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    model_short = model.replace(":", "-").replace("/", "-")
    mode = "fast" if args.topic_only else "full"
    result_file = RESULTS_DIR / f"topic_{date_str}_{tag}_{model_short}_{mode}.json"

    output = {
        "type": "topic",
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "prompt_tag": tag,
        "mode": mode,
        "ollama_url": OLLAMA_URL,
        "eval_set_version": eval_version,
        "eval_set_path": str(eval_path),
        "metrics": metrics,
        "results": results,
    }

    result_file.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nResults saved: {result_file}")


if __name__ == "__main__":
    main()
