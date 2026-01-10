#!/usr/bin/env python3
"""
Process borderline items with LLM to get recommendations.

Reads borderline_candidates.json and processes each with qwen3:14b-q8_0
to get the LLM's assessment.
"""

import json
import os
import sys
import time
from pathlib import Path
import requests

PROJECT_ROOT = Path(__file__).parent.parent
OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:14b-q8_0"

# Note: qwen3 models don't work well with system prompts via API
# Everything is included in the prompt instead


def process_item(item: dict) -> dict:
    """Process single item with LLM."""
    text = item.get("text", item.get("title", ""))[:4000]
    title = item.get("title", "")[:200]

    # qwen3 needs everything in the prompt (not system)
    prompt = f"""Du bist ein Experte für Sozialpolitik in Hessen. Bewerte, ob dieser Artikel für die Liga der Freien Wohlfahrtspflege Hessen relevant ist.

Die Liga ist der Dachverband von: AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden.

RELEVANT wenn es um eines dieser Themen geht:
- Sozialpolitik in Hessen (Haushalte, Förderungen, Gesetze)
- Pflege, Gesundheit, Senioren
- Migration, Flucht, Integration
- Menschen mit Behinderungen, Inklusion
- Kinder, Jugend, Kitas, Familie, Frauen
- Wohnungslosigkeit, Sucht, Schulden
- Wohlfahrtsverbände, Ehrenamt

NICHT RELEVANT:
- Allgemeine Politik ohne Sozialbezug
- Sport, Unterhaltung, Klatsch
- Reine Lokalnachrichten ohne Sozialbezug
- Wirtschaft ohne Sozialbezug

Titel: {title}

Inhalt: {text}

Antworte NUR mit JSON: {{"relevant": true/false, "reasoning": "kurze Begründung"}}"""

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3}
            },
            timeout=60
        )
        resp.raise_for_status()

        response_text = resp.json().get("response", "")

        # Extract JSON from response
        try:
            # Try to find JSON in response
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = response_text[start:end]
                result = json.loads(json_str)
                return {
                    "llm_relevant": result.get("relevant", None),
                    "reasoning": result.get("reasoning", ""),
                    "raw_response": response_text
                }
        except json.JSONDecodeError:
            pass

        return {"llm_relevant": None, "reasoning": "", "raw_response": response_text, "parse_error": True}

    except Exception as e:
        return {"llm_relevant": None, "reasoning": "", "error": str(e)}


def main():
    input_file = PROJECT_ROOT / "data" / "borderline_candidates.json"
    output_file = PROJECT_ROOT / "data" / "borderline_llm_results.json"

    print(f"Loading {input_file}")
    with open(input_file, "r", encoding="utf-8") as f:
        items = json.load(f)

    print(f"Processing {len(items)} items with {MODEL}...")
    print("-" * 60)

    results = []
    start_time = time.time()

    for i, item in enumerate(items):
        print(f"[{i+1}/{len(items)}] idx={item['idx']}: {item['title'][:60]}...")

        llm_result = process_item(item)

        result = {
            "idx": item["idx"],
            "title": item["title"][:100],
            "current_relevant": item["current_relevant"],
            "suggested_relevant": item["suggested_relevant"],
            "neighbor_agreement": item["neighbor_agreement"],
            "llm_relevant": llm_result.get("llm_relevant"),
            "reasoning": llm_result.get("reasoning", ""),
        }

        if llm_result.get("parse_error") or llm_result.get("error"):
            result["error"] = llm_result.get("error", "parse_error")

        results.append(result)

        # Show result
        curr = "REL" if item["current_relevant"] else "IRR"
        sugg = "REL" if item["suggested_relevant"] else "IRR"
        llm = "REL" if llm_result.get("llm_relevant") else ("IRR" if llm_result.get("llm_relevant") == False else "???")

        print(f"    Current: {curr} | Neighbors suggest: {sugg} | LLM says: {llm}")
        if llm_result.get("reasoning"):
            print(f"    Reason: {llm_result['reasoning'][:80]}")
        print()

        # Save intermediate results
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - start_time
    print("=" * 60)
    print(f"Processed {len(items)} items in {elapsed:.1f}s ({elapsed/len(items):.1f}s per item)")
    print(f"Results saved to: {output_file}")

    # Summary
    agree_current = sum(1 for r in results if r.get("llm_relevant") == bool(r["current_relevant"]))
    agree_suggest = sum(1 for r in results if r.get("llm_relevant") == bool(r["suggested_relevant"]))
    errors = sum(1 for r in results if r.get("llm_relevant") is None)

    print(f"\nLLM agrees with current label: {agree_current}/{len(items)}")
    print(f"LLM agrees with neighbor suggestion: {agree_suggest}/{len(items)}")
    print(f"Parse errors: {errors}")


if __name__ == "__main__":
    main()
