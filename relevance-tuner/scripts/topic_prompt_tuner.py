#!/usr/bin/env python3
"""Interactive topic prompt tuner — iterate fast on disambiguation rules.

Calls Ollama directly, captures thinking + response, shows side-by-side
comparison of prompt variants on stubborn items.

Usage:
    # Run all misclassified items with current prompt
    python scripts/topic_prompt_tuner.py

    # Run specific items
    python scripts/topic_prompt_tuner.py --ids 2448,2165,7128

    # Run with a prompt file (YAML)
    python scripts/topic_prompt_tuner.py --prompt prompts/v8.yaml

    # Compare two prompt variants
    python scripts/topic_prompt_tuner.py --compare prompts/v7.yaml prompts/v8.yaml

    # Only run items where expected topic is Sozialpolitik or got Sozialpolitik
    python scripts/topic_prompt_tuner.py --filter-topic Sozialpolitik

    # Show thinking for specific items
    python scripts/topic_prompt_tuner.py --ids 2448 --show-thinking
"""

import argparse
import json
import os
import re
import sys
import time
import textwrap
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# Paths
ROOT = Path(__file__).parent.parent
NEWS_ROOT = ROOT.parent / "news-aggregator"
EVAL_SET = ROOT / "evaluations" / "topic_eval_set.json"
PROMPTS_DIR = ROOT / "prompts"
RESULTS_DIR = ROOT / "evaluations" / "results"

sys.path.insert(0, str(NEWS_ROOT / "backend" / "services"))
from topic_taxonomy import TOPIC_TAXONOMY, SONSTIGES, validate_topic

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:14b-q8_0")

def _set_model(m: str):
    global MODEL
    MODEL = m

# ANSI colors
C_RED = "\033[91m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_BLUE = "\033[94m"
C_DIM = "\033[2m"
C_BOLD = "\033[1m"
C_RESET = "\033[0m"


# ============================================================================
# System prompt (extracted from processor.py)
# ============================================================================

def get_system_prompt() -> str:
    processor = NEWS_ROOT / "backend" / "services" / "processor.py"
    content = processor.read_text()
    m = re.search(r'ANALYSIS_SYSTEM_PROMPT = """(.*?)"""', content, re.DOTALL)
    if not m:
        print("ERROR: Cannot find ANALYSIS_SYSTEM_PROMPT in processor.py")
        sys.exit(1)
    return m.group(1).strip()


# ============================================================================
# Prompt loading
# ============================================================================

def default_prompt() -> str:
    """Build the topic follow-up prompt matching current processor.py."""
    processor = NEWS_ROOT / "backend" / "services" / "processor.py"
    content = processor.read_text()

    # Extract the follow_up content string from processor.py
    # Look for the topic_follow_up content between the markers
    m = re.search(
        r'follow_up = \{.*?"content":\s*\((.*?)\),\s*\}',
        content, re.DOTALL
    )
    if not m:
        # Fallback: build from taxonomy
        taxonomy_list = "\n".join(f"- {t}" for t in TOPIC_TAXONOMY)
        return (
            "Ordne diesen Artikel GENAU EINEM Thema aus der folgenden Liste zu.\n\n"
            f"THEMENLISTE:\n{taxonomy_list}\n- Sonstiges\n\n"
            "REGELN:\n"
            "- Wähle das Thema, das am besten beschreibt, WARUM der Artikel für die "
            "Wohlfahrtspflege relevant ist.\n"
            "- KEINE Parteinamen, Organisationsnamen oder Ortsnamen als Thema.\n"
            "- Nur Sonstiges wählen, wenn wirklich KEIN Thema passt.\n\n"
            "Antwort NUR als JSON:\n"
            '{"topic": "Thema aus Liste"}\n'
            "oder bei Sonstiges:\n"
            '{"topic": "Sonstiges", "topic_suggestion": "Dein Vorschlag"}'
        )

    # We can't easily eval the Python string concat, so just read the function
    # that run_topic_eval.py uses: get_topic_follow_up_prompt
    # Instead, let's just use the taxonomy and build it ourselves from YAML
    return None  # signals to use from processor


def load_prompt(path: str | None) -> str:
    """Load prompt from YAML file or use default."""
    if path is None:
        return build_prompt_from_processor()

    p = Path(path)
    if not p.exists():
        # Try prompts dir
        p = PROMPTS_DIR / path
        if not p.exists():
            print(f"ERROR: Prompt file not found: {path}")
            sys.exit(1)

    if not HAS_YAML:
        print("ERROR: PyYAML required for prompt files. pip install pyyaml")
        sys.exit(1)

    data = yaml.safe_load(p.read_text())
    return build_prompt_from_yaml(data)


def build_prompt_from_processor() -> str:
    """Extract the exact prompt from processor.py by reading the taxonomy
    list construction and the content string."""
    processor = NEWS_ROOT / "backend" / "services" / "processor.py"
    content = processor.read_text()

    # Find the taxonomy list construction
    # Check if Sozialpolitik is excluded
    if 'if t != "Sozialpolitik"' in content:
        taxonomy_list = "\n".join(f"- {t}" for t in TOPIC_TAXONOMY if t != "Sozialpolitik")
    elif 'if t == "Sozialpolitik"' in content:
        taxonomy_list = "\n".join(
            f"- {t} ⚠ NUR als letzter Ausweg — zuerst spezifischeres Thema prüfen!"
            if t == "Sozialpolitik" else f"- {t}"
            for t in TOPIC_TAXONOMY
        )
    else:
        taxonomy_list = "\n".join(f"- {t}" for t in TOPIC_TAXONOMY)

    # Extract the raw string content between follow_up content: ( ... )
    # This is fragile, so we use a regex approach
    m = re.search(
        r'("Ordne diesen Artikel.*?topic_suggestion.*?Vorschlag.*?")',
        content, re.DOTALL
    )
    if m:
        # We found the string block. Now we need to evaluate the Python string concatenation.
        # Instead, let's just exec it in a sandbox with taxonomy_list defined.
        raw = m.group(1)
        # Replace f-string refs
        raw = raw.replace("{taxonomy_list}", taxonomy_list)
        try:
            # Evaluate the concatenated string
            result = eval(raw, {"taxonomy_list": taxonomy_list})
            return result
        except Exception:
            pass

    # If extraction failed, fall back to a clean version
    print(f"{C_YELLOW}WARNING: Could not extract prompt from processor.py, using fallback{C_RESET}")
    return _fallback_prompt(taxonomy_list)


def _fallback_prompt(taxonomy_list: str) -> str:
    return (
        "Ordne diesen Artikel GENAU EINEM Thema aus der folgenden Liste zu.\n\n"
        f"THEMENLISTE:\n{taxonomy_list}\n- Sonstiges\n\n"
        "REGELN:\n"
        "- Wähle das Thema, das am besten beschreibt, WARUM der Artikel für die "
        "Wohlfahrtspflege relevant ist — nicht worum es allgemein geht.\n"
        "- Bei thematischer Überschneidung wähle das ENGERE, SPEZIFISCHERE Thema.\n"
        "- KEINE Parteinamen, Organisationsnamen oder Ortsnamen als Thema.\n"
        "- Nur Sonstiges wählen, wenn wirklich KEIN Thema passt.\n\n"
        "Antwort NUR als JSON:\n"
        '{"topic": "Thema aus Liste"}\n'
        "oder bei Sonstiges:\n"
        '{"topic": "Sonstiges", "topic_suggestion": "Dein Vorschlag"}'
    )


def build_prompt_from_yaml(data: dict) -> str:
    """Build topic prompt from YAML config.

    YAML format:
        topics:              # optional override (default: use TOPIC_TAXONOMY)
          exclude: [Sozialpolitik]
          # or: include_only: [...]
        rules:
          - "Rule text 1"
          - "Rule text 2"
        distinctions:
          - "Tarifpolitik = ..."
          - "Senioren und Alter = ..."
        examples:
          - input: "Mindestlohn steigt"
            topic: Tarifpolitik
          - input: "Warnstreiks in Kitas"
            topic: Tarifpolitik
        fallback_topics:     # topics not in main list but available as last resort
          - name: Sozialpolitik
            description: "NUR für übergreifende Sozialstaats-Debatten"
          - name: Sonstiges
            description: "mit topic_suggestion"
        json_format: true    # default true
    """
    # Build topic list
    topics = list(TOPIC_TAXONOMY)
    tc = data.get("topics", {})
    if isinstance(tc, dict):
        exclude = tc.get("exclude", [])
        if exclude:
            topics = [t for t in topics if t not in exclude]

    taxonomy_list = "\n".join(f"- {t}" for t in topics)

    parts = [
        "Ordne diesen Artikel GENAU EINEM Thema aus der folgenden Liste zu.\n",
        f"THEMENLISTE:\n{taxonomy_list}\n",
    ]

    # Rules
    rules = data.get("rules", [])
    if rules:
        parts.append("REGELN:")
        for r in rules:
            parts.append(f"- {r}")
        parts.append("")

    # Distinctions
    distinctions = data.get("distinctions", [])
    if distinctions:
        parts.append("UNTERSCHEIDUNGEN:")
        for d in distinctions:
            parts.append(f"- {d}")
        parts.append("")

    # Examples
    examples = data.get("examples", [])
    if examples:
        parts.append("BEISPIELE:")
        for ex in examples:
            parts.append(f"- \u201e{ex['input']}\u201c \u2192 {ex['topic']}")
        parts.append("")

    # Fallback topics
    fallbacks = data.get("fallback_topics", [])
    if fallbacks:
        parts.append("Wenn KEIN Thema aus der Liste passt, prüfe:")
        for fb in fallbacks:
            parts.append(f"- {fb['name']} — {fb.get('description', '')}")
        parts.append("")

    # JSON format
    if data.get("json_format", True):
        parts.append("Antwort NUR als JSON:")
        parts.append('{"topic": "Thema aus Liste"}')
        parts.append("oder bei Sonstiges:")
        parts.append('{"topic": "Sonstiges", "topic_suggestion": "Dein Vorschlag"}')

    return "\n".join(parts)


# ============================================================================
# Ollama API
# ============================================================================

def call_ollama(messages: list[dict], think: bool = True) -> dict:
    """Call Ollama chat API, return response + thinking."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "think": think,
        # Production uses think=False, num_predict=150 for topic classification
        "options": {"temperature": 0.2, "num_predict": 1024 if think else 150},
    }

    start = time.time()
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=180)
        elapsed = time.time() - start
        data = r.json()
        msg = data.get("message", {})

        thinking = msg.get("thinking", "")
        response = msg.get("content", "")

        # Fallback: some Ollama versions put thinking in content with tags
        if not thinking and "<think>" in response:
            m = re.search(r"<think>(.*?)</think>", response, re.DOTALL)
            if m:
                thinking = m.group(1)
                response = response.split("</think>", 1)[-1].strip()

        eval_count = data.get("eval_count", 0)
        eval_duration = data.get("eval_duration", 0)
        tok_per_sec = eval_count / (eval_duration / 1e9) if eval_duration > 0 else 0

        return {
            "thinking": thinking.strip(),
            "response": response.strip(),
            "wall_time_s": round(elapsed, 1),
            "tok_per_sec": round(tok_per_sec, 1),
            "error": None,
        }
    except Exception as e:
        return {
            "thinking": "",
            "response": "",
            "wall_time_s": round(time.time() - start, 1),
            "tok_per_sec": 0,
            "error": str(e),
        }


# ============================================================================
# Prompt execution
# ============================================================================

def parse_topic_response(text: str) -> dict:
    """Parse JSON topic from response."""
    clean = re.sub(r'```json\s*', '', text)
    clean = re.sub(r'```\s*', '', clean)
    start = clean.find("{")
    end = clean.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(clean[start:end])
            raw = data.get("topic", "")
            topic, suggestion = validate_topic(raw)
            return {"topic": topic, "suggestion": suggestion or data.get("topic_suggestion"), "ok": True}
        except json.JSONDecodeError:
            pass
    return {"topic": SONSTIGES, "suggestion": None, "ok": False}


def run_item(item: dict, prompt: str, system_prompt: str, think: bool = True) -> dict:
    """Run a single item through the model with the given prompt."""
    content = item.get("content", "")[:2000]
    user_msg = f"Analysiere diesen Nachrichtenartikel:\n\nTitel: {item['title']}\n\nInhalt: {content}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": '{"relevant": true, "summary": "..."}'},
        {"role": "user", "content": prompt},
    ]

    result = call_ollama(messages, think=think)
    parsed = parse_topic_response(result["response"])

    return {
        "id": item["id"],
        "title": item["title"],
        "expected": item["ground_truth"]["topic"],
        "got": parsed["topic"],
        "correct": parsed["topic"] == item["ground_truth"]["topic"],
        "thinking": result["thinking"],
        "raw_response": result["response"],
        "time_s": result["wall_time_s"],
        "tok_per_sec": result["tok_per_sec"],
        "error": result["error"],
    }


# ============================================================================
# Display
# ============================================================================

def print_result(r: dict, show_thinking: bool = False, compact: bool = False):
    """Print a single result."""
    status = f"{C_GREEN}✓{C_RESET}" if r["correct"] else f"{C_RED}✗{C_RESET}"
    title = r["title"][:70]

    if compact:
        exp = r["expected"][:25]
        got = r["got"][:25]
        print(f"  {status} {r['id']:>6} {exp:25} → {got:25} {r['time_s']:>5.1f}s  {title}")
        return

    print(f"\n{status} Item {r['id']}: {title}")
    print(f"  Expected: {C_BLUE}{r['expected']}{C_RESET}")
    got_color = C_GREEN if r["correct"] else C_RED
    print(f"  Got:      {got_color}{r['got']}{C_RESET}  ({r['time_s']:.1f}s)")

    if show_thinking and r["thinking"]:
        print(f"\n  {C_DIM}--- Thinking ---{C_RESET}")
        for line in r["thinking"].split("\n"):
            print(f"  {C_DIM}{line}{C_RESET}")
        print(f"  {C_DIM}--- End thinking ---{C_RESET}")

    if r["error"]:
        print(f"  {C_RED}Error: {r['error']}{C_RESET}")


def print_summary(results: list[dict], label: str = ""):
    """Print aggregate stats."""
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    acc = correct / total * 100 if total else 0

    print(f"\n{'='*70}")
    if label:
        print(f"  {C_BOLD}{label}{C_RESET}")
    print(f"  Accuracy: {C_BOLD}{acc:.1f}%{C_RESET} ({correct}/{total})")

    # Per-topic errors
    errors = [r for r in results if not r["correct"]]
    if errors:
        confusions = Counter((r["expected"], r["got"]) for r in errors)
        print(f"\n  Errors ({len(errors)}):")
        for (exp, got), count in confusions.most_common(15):
            print(f"    {exp:30} → {got:30} ({count}x)")

    # Topics with 0% recall
    expected_counts = Counter(r["expected"] for r in results)
    correct_counts = Counter(r["expected"] for r in results if r["correct"])
    zero_recall = [t for t, c in expected_counts.items() if correct_counts.get(t, 0) == 0]
    if zero_recall:
        print(f"\n  {C_RED}Zero recall topics: {', '.join(zero_recall)}{C_RESET}")

    # Sozialpolitik FP
    sozfp = sum(1 for r in results if r["got"] == "Sozialpolitik" and r["expected"] != "Sozialpolitik")
    if sozfp:
        print(f"  {C_YELLOW}Sozialpolitik FP: {sozfp}{C_RESET}")

    avg_time = sum(r["time_s"] for r in results) / total if total else 0
    print(f"  Avg time: {avg_time:.1f}s")
    print(f"{'='*70}")


def print_comparison(results_a: list[dict], results_b: list[dict],
                     label_a: str, label_b: str):
    """Print side-by-side comparison of two runs."""
    by_id_a = {r["id"]: r for r in results_a}
    by_id_b = {r["id"]: r for r in results_b}

    all_ids = sorted(set(by_id_a) | set(by_id_b))

    gained = []
    lost = []
    for id in all_ids:
        a = by_id_a.get(id)
        b = by_id_b.get(id)
        if a and b:
            if not a["correct"] and b["correct"]:
                gained.append((id, a, b))
            elif a["correct"] and not b["correct"]:
                lost.append((id, a, b))

    acc_a = sum(1 for r in results_a if r["correct"]) / len(results_a) * 100
    acc_b = sum(1 for r in results_b if r["correct"]) / len(results_b) * 100

    print(f"\n{'='*70}")
    print(f"  COMPARISON: {label_a} vs {label_b}")
    print(f"  {label_a}: {acc_a:.1f}%  →  {label_b}: {acc_b:.1f}%  (Δ {acc_b - acc_a:+.1f}%)")
    print(f"{'='*70}")

    if gained:
        print(f"\n  {C_GREEN}GAINED ({len(gained)}):{C_RESET}")
        for id, a, b in gained:
            print(f"    {id}: {a['expected']} (was: {a['got']} → now: {b['got']})")

    if lost:
        print(f"\n  {C_RED}LOST ({len(lost)}):{C_RESET}")
        for id, a, b in lost:
            print(f"    {id}: {a['expected']} (was: {a['got']} → now: {b['got']})")

    if not gained and not lost:
        print(f"\n  {C_DIM}No changes between variants{C_RESET}")


# ============================================================================
# Prompt file management
# ============================================================================

def save_default_prompt():
    """Save the current processor.py prompt as a YAML template."""
    if not HAS_YAML:
        print("PyYAML not installed. pip install pyyaml")
        return

    PROMPTS_DIR.mkdir(exist_ok=True)
    template = {
        "name": "current",
        "description": "Current processor.py prompt (auto-extracted)",
        "topics": {"exclude": ["Sozialpolitik"]},
        "rules": [
            "Wähle das Thema, das am besten beschreibt, WARUM der Artikel für die Wohlfahrtspflege relevant ist — nicht worum es allgemein geht.",
            "Bei thematischer Überschneidung wähle das ENGERE, SPEZIFISCHERE Thema.",
            "KEINE Parteinamen, Organisationsnamen oder Ortsnamen als Thema.",
        ],
        "distinctions": [
            "Tarifpolitik = Tarifvertrag, Warnstreik, Arbeitskampf, Mindestlohn, Lohnerhöhung — auch wenn in Kitas/Krankenhäusern gestreikt wird",
            "Senioren und Alter = Rente, Altersarmut, Alterssicherung, Rentenreform",
            "Fachkräftemangel = struktureller Personalmangel, Fachkräftelücke",
            "Pflege = Pflegepersonal, Pflegebeitrag, Pflegereform",
            "Bürokratieabbau = Entbürokratisierung, Regulierungsabbau",
            "Gesundheitsversorgung = Krankenhausreform, Klinikschließung, Krankenkasse",
            "Migration und Flucht = auch Abschiebung, Asylpolitik, Menschenschmuggel",
        ],
        "examples": [
            {"input": "Mindestlohn steigt auf 13,90€", "topic": "Tarifpolitik"},
            {"input": "Warnstreiks in Kitas und Unikliniken", "topic": "Tarifpolitik"},
            {"input": "Steuerbefreiung für Gewerkschaftsbeiträge", "topic": "Tarifpolitik"},
            {"input": "200 Mrd. für Rentenleistungen", "topic": "Senioren und Alter"},
            {"input": "Alterssicherungskommission konstituiert", "topic": "Senioren und Alter"},
            {"input": "Pflegebeitrag stoppen, Strukturreform", "topic": "Pflege"},
            {"input": "CDU will Bürokratieabbau für Unternehmen", "topic": "Bürokratieabbau"},
        ],
        "fallback_topics": [
            {"name": "Sozialpolitik", "description": "NUR für übergreifende Sozialstaats-Debatten ohne klaren Fachbezug"},
            {"name": "Sonstiges", "description": "mit topic_suggestion"},
        ],
    }

    out = PROMPTS_DIR / "current.yaml"
    with open(out, "w") as f:
        yaml.dump(template, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"Saved: {out}")
    return out


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Interactive topic prompt tuner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              %(prog)s                           # run all misclassified items
              %(prog)s --ids 2448,2165,7128       # specific items
              %(prog)s --prompt prompts/v8.yaml   # custom prompt
              %(prog)s --compare v7.yaml v8.yaml  # compare two prompts
              %(prog)s --filter-topic Sozialpolitik
              %(prog)s --show-thinking --ids 2448
              %(prog)s --init-prompt              # save current prompt as YAML
        """),
    )
    parser.add_argument("--ids", type=str, help="Comma-separated item IDs to test")
    parser.add_argument("--prompt", type=str, help="YAML prompt file (or path in prompts/)")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"),
                        help="Compare two prompt files")
    parser.add_argument("--filter-topic", type=str,
                        help="Only items where expected or got matches this topic")
    parser.add_argument("--misses-only", action="store_true", default=True,
                        help="Only run previously misclassified items (default)")
    parser.add_argument("--all", action="store_true",
                        help="Run all items (not just misses)")
    parser.add_argument("--show-thinking", action="store_true",
                        help="Show model thinking for each item")
    parser.add_argument("--think", action="store_true",
                        help="Enable thinking mode (default: off, matching production)")
    parser.add_argument("--show-prompt", action="store_true",
                        help="Print the generated prompt and exit")
    parser.add_argument("--compact", action="store_true",
                        help="Compact output (one line per item)")
    parser.add_argument("--init-prompt", action="store_true",
                        help="Save current prompt as YAML template and exit")
    parser.add_argument("--model", type=str, help=f"Override model (default: {MODEL})")
    parser.add_argument("--limit", type=int, help="Max items to run")
    parser.add_argument("--save", type=str, help="Save results to JSON file")
    args = parser.parse_args()

    model = args.model or MODEL
    _set_model(model)

    if args.init_prompt:
        save_default_prompt()
        return

    if args.show_prompt:
        prompt = load_prompt(args.prompt)
        print(prompt)
        return

    # Load eval set
    if not EVAL_SET.exists():
        print(f"ERROR: {EVAL_SET} not found")
        sys.exit(1)

    eval_data = json.loads(EVAL_SET.read_text())
    items = [i for i in eval_data["items"] if i.get("ground_truth")]
    items_by_id = {str(i["id"]): i for i in items}

    # Filter items
    if args.ids:
        ids = [s.strip() for s in args.ids.split(",")]
        items = [items_by_id[id] for id in ids if id in items_by_id]
        missing = [id for id in ids if id not in items_by_id]
        if missing:
            print(f"{C_YELLOW}Warning: IDs not in eval set: {missing}{C_RESET}")
    elif args.filter_topic:
        ft = args.filter_topic
        # Load latest eval results to know which were misclassified
        latest = _load_latest_results()
        if latest and not args.all:
            misses = {str(r["id"]) for r in latest if not r["correct_topic"]}
            items = [i for i in items
                     if (str(i["id"]) in misses and
                         (i["ground_truth"]["topic"] == ft or
                          _result_topic(latest, i["id"]) == ft))]
        else:
            items = [i for i in items if i["ground_truth"]["topic"] == ft]
    elif not args.all:
        # Default: only misclassified items from latest run
        latest = _load_latest_results()
        if latest:
            misses = {str(r["id"]) for r in latest if not r["correct_topic"]}
            items = [i for i in items if str(i["id"]) in misses]
        else:
            print(f"{C_YELLOW}No previous results found, running all items{C_RESET}")

    if args.limit:
        items = items[:args.limit]

    if not items:
        print("No items to evaluate")
        return

    system_prompt = get_system_prompt()

    # Compare mode
    if args.compare:
        prompt_a = load_prompt(args.compare[0])
        prompt_b = load_prompt(args.compare[1])

        print(f"\n{C_BOLD}Running {len(items)} items with prompt A: {args.compare[0]}{C_RESET}")
        results_a = _run_batch(items, prompt_a, system_prompt, args)

        print(f"\n{C_BOLD}Running {len(items)} items with prompt B: {args.compare[1]}{C_RESET}")
        results_b = _run_batch(items, prompt_b, system_prompt, args)

        print_comparison(results_a, results_b, args.compare[0], args.compare[1])
        print_summary(results_a, args.compare[0])
        print_summary(results_b, args.compare[1])
        return

    # Single prompt mode
    prompt = load_prompt(args.prompt)
    label = args.prompt or "processor.py"

    print(f"\n{C_BOLD}Topic Prompt Tuner{C_RESET}")
    print(f"  Model: {MODEL}")
    print(f"  Prompt: {label}")
    print(f"  Items: {len(items)}")
    print(f"  Think: {'on' if args.think else 'off (production)'}")

    results = _run_batch(items, prompt, system_prompt, args)
    print_summary(results, label)

    if args.save:
        _save_results(results, args.save, label)


def _run_batch(items: list, prompt: str, system_prompt: str, args) -> list:
    """Run a batch of items and display results."""
    results = []
    think = args.think

    for idx, item in enumerate(items, 1):
        gt_topic = item["ground_truth"]["topic"]
        print(f"\r  [{idx}/{len(items)}] Item {item['id']}: {item['title'][:50]}...", end="", flush=True)

        r = run_item(item, prompt, system_prompt, think=think)
        results.append(r)

        # Clear line and print result
        print(f"\r{' '*80}\r", end="")
        print_result(r, show_thinking=args.show_thinking, compact=args.compact)

    return results


def _load_latest_results() -> list | None:
    """Load the most recent topic eval results."""
    if not RESULTS_DIR.exists():
        return None

    topic_files = sorted(RESULTS_DIR.glob("topic_*.json"), reverse=True)
    for f in topic_files:
        try:
            data = json.loads(f.read_text())
            return data.get("results", [])
        except Exception:
            continue
    return None


def _result_topic(results: list, item_id: int) -> str | None:
    """Get the predicted topic for an item from results."""
    for r in results:
        if r["id"] == item_id:
            return r.get("got_topic")
    return None


def _save_results(results: list, path: str, label: str):
    """Save results to JSON."""
    out = {
        "label": label,
        "model": MODEL,
        "timestamp": datetime.now().isoformat(),
        "total": len(results),
        "correct": sum(1 for r in results if r["correct"]),
        "accuracy": sum(1 for r in results if r["correct"]) / len(results) if results else 0,
        "results": results,
    }
    Path(path).write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nSaved to {path}")


if __name__ == "__main__":
    main()
