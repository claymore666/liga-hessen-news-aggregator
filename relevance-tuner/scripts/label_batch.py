#!/usr/bin/env python3
"""
Reproducible batch labeling script for Liga Hessen relevance classifier.

Usage:
    python scripts/label_batch.py --batch 0
    python scripts/label_batch.py --all

This script uses the prompt from LABELING_PROMPT.md to ensure consistent labeling.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
PROMPT_FILE = PROJECT_ROOT / "LABELING_PROMPT.md"
BATCHES_DIR = PROJECT_ROOT / "data" / "raw" / "batches"
OUTPUT_DIR = PROJECT_ROOT / "data" / "reviewed" / "agent_results"

def load_prompt() -> str:
    """Load the labeling prompt from LABELING_PROMPT.md"""
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract the system prompt between ```
    start = content.find("```\n", content.find("## System Prompt"))
    end = content.find("```", start + 4)

    if start == -1 or end == -1:
        raise ValueError("Could not find system prompt in LABELING_PROMPT.md")

    return content[start + 4:end].strip()


def load_batch(batch_num: int) -> list[dict]:
    """Load items from a batch file."""
    batch_file = BATCHES_DIR / f"batch_{batch_num:02d}.jsonl"

    if not batch_file.exists():
        raise FileNotFoundError(f"Batch file not found: {batch_file}")

    items = []
    with open(batch_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))

    return items


def format_items_for_labeling(items: list[dict]) -> str:
    """Format items as text for the labeling agent."""
    lines = []
    for i, item in enumerate(items, 1):
        inp = item.get("input", {})
        lines.append(f"=== ARTIKEL {i} ===")
        lines.append(f"Titel: {inp.get('title', 'N/A')}")
        lines.append(f"Quelle: {inp.get('source', 'N/A')}")
        lines.append(f"Datum: {inp.get('date', 'N/A')}")
        lines.append(f"Inhalt: {inp.get('content', 'N/A')[:1500]}")
        lines.append("")

    return "\n".join(lines)


def create_agent_prompt(system_prompt: str, items_text: str) -> str:
    """Create the full prompt for the labeling agent."""
    return f"""{system_prompt}

=== AUFGABE ===

Analysiere die folgenden {items_text.count('=== ARTIKEL')} Artikel und gib für jeden eine JSON-Zeile aus.

{items_text}

=== AUSGABE ===

Gib für JEDEN Artikel genau EINE JSON-Zeile aus im Format:
{{"title": "Originaltitel", "relevant": true/false, "ak": "AK1"|"AK2"|"AK3"|"AK4"|"AK5"|"QAG"|null, "priority": "critical"|"high"|"medium"|"low"|null, "reasoning": "Kurze Begründung"}}

Beginne jetzt mit der Analyse:"""


def get_batch_count() -> int:
    """Count available batches."""
    return len(list(BATCHES_DIR.glob("batch_*.jsonl")))


def main():
    parser = argparse.ArgumentParser(description="Label batches for Liga relevance classifier")
    parser.add_argument("--batch", type=int, help="Batch number to process (0-indexed)")
    parser.add_argument("--all", action="store_true", help="Process all batches")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without executing")
    args = parser.parse_args()

    if not args.batch and not args.all:
        parser.error("Either --batch N or --all must be specified")

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load prompt
    print(f"Loading prompt from {PROMPT_FILE}...")
    system_prompt = load_prompt()
    print(f"Prompt loaded ({len(system_prompt)} chars)")

    # Determine batches to process
    if args.all:
        batch_nums = list(range(get_batch_count()))
    else:
        batch_nums = [args.batch]

    print(f"Will process {len(batch_nums)} batch(es): {batch_nums}")

    for batch_num in batch_nums:
        print(f"\n{'='*60}")
        print(f"Processing batch {batch_num:02d}")
        print(f"{'='*60}")

        # Load batch
        items = load_batch(batch_num)
        print(f"Loaded {len(items)} items")

        # Format for labeling
        items_text = format_items_for_labeling(items)
        full_prompt = create_agent_prompt(system_prompt, items_text)

        if args.dry_run:
            print(f"\n--- PROMPT ({len(full_prompt)} chars) ---")
            print(full_prompt[:2000])
            print("...")
            continue

        # Output file
        output_file = OUTPUT_DIR / f"batch_{batch_num:02d}_labeled.jsonl"

        print(f"Output will be written to: {output_file}")
        print(f"Prompt size: {len(full_prompt)} chars")
        print("Launch labeling agent with Claude Code Task tool...")

        # The actual labeling happens via Claude Code agents
        # This script prepares the prompt and documents the methodology

        print(f"\nTo label this batch, use Claude Code:")
        print(f"  Read: {BATCHES_DIR}/batch_{batch_num:02d}.jsonl")
        print(f"  Write to: {output_file}")
        print(f"  Use prompt from: {PROMPT_FILE}")


if __name__ == "__main__":
    main()
