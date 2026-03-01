#!/usr/bin/env python3
"""
Benchmark small Qwen models as a fast pre-filter for relevance classification.

Pipeline idea: classifier → mini-model (title only) → full LLM
This script tests whether small models can quickly reject false positives
from the classifier using only the title.

Usage:
    python scripts/benchmark_mini_models.py
"""

import json
import time
import requests
import sys
from dataclasses import dataclass

OLLAMA_URL = "http://localhost:11434"
MODELS = ["qwen3:1.7b", "qwen3:4b", "qwen3:8b"]

# Ground truth: items from weekend classifier-only batch, labeled by Haiku
# 1 = relevant to Liga (social welfare policy), 0 = irrelevant
TEST_ITEMS = [
    # === CLEARLY IRRELEVANT (false positives from classifier) ===
    {"title": "CDU-Stammtisch in Volkmarsen: Kanzler Merz im letzten Wirtshaus vor Washington", "relevant": 0},
    {"title": "Neue Koalition steht in den Startlöchern: SPD und CDU wollen Brandenburg mit Beinfreiheit regieren", "relevant": 0},
    {"title": "AfD: Viele ausländische Mitbürger am Info-Stand", "relevant": 0},
    {"title": "Kommunalwahl Wiesbaden 2025: Alles Wichtige zur Stimmabgabe", "relevant": 0},
    {"title": "KI-Jobplattform: Was steckt hinter Rent a Human?", "relevant": 0},
    {"title": "Solarer Vollstopp, vorgetäuschte Heiz-Freiheit und eine Koalition, die von RWE abschreibt", "relevant": 0},
    {"title": "SPD und CDU in Brandenburg auf der Zielgeraden für Koalition", "relevant": 0},
    {"title": "Große Koalition der Satiriker von ARD und ZDF bei phoenix", "relevant": 0},
    {"title": "Rätseln und gewinnen!: Kreuzworträtsel", "relevant": 0},
    {"title": "jW-Wochenendgeschichte: Ein dramatisches Leben", "relevant": 0},
    {"title": "Gedicht zeigen: Erbschaftssteuer", "relevant": 0},
    {"title": "Großbritannien: Quittung für Labour", "relevant": 0},
    {"title": "Brief aus Jerusalem: Ein Ort für alle", "relevant": 0},
    {"title": "Arbeitskampf in Argentinien: Wie wird das Streikrecht durch Milei beschnitten?", "relevant": 0},
    {"title": "Bundeskanzler in China: Merz zurück auf Los!", "relevant": 0},
    {"title": "Kommentar: Ich will Claus zurück!", "relevant": 0},
    {"title": "Gegen Militarisierung: Der Jugend eine Zukunft!", "relevant": 0},
    {"title": "Verteilen: Zeitung in die Hand, Frieden auf die Straße!", "relevant": 0},
    {"title": "Brandenburg: SPD und CDU vor Einigung auf Koalition", "relevant": 0},
    {"title": "AfD-Verbotsverfahren: Ich bin sicher, dass Karlsruhe anders abbiegen wird", "relevant": 0},
    {"title": "Beschluss in Köln: Verfassungsschutz darf AfD vorerst nicht als gesichert rechtsextrem einstufen", "relevant": 0},
    {"title": "CDU-Fraktionschef in Raunheim erleidet Schwächeanfall", "relevant": 0},
    {"title": "Koalitionsrechner zur Landtagswahl 2026 in Baden-Württemberg: Welche Bündnisse sind möglich?", "relevant": 0},
    {"title": "Kommunalwahl in Fuldatal: Linke und Freie Wähler treten erstmalig an", "relevant": 0},
    {"title": "Merz und Rhein starten Kommunalwahl-Endspurt in Volkmarsen", "relevant": 0},
    {"title": "Fünf Kandidaten, fünf Profile: Wer wird neuer Bürgermeister der Kreisstadt?", "relevant": 0},
    {"title": "7 Fakten zu Geburten: So kommt Hessen zur Welt", "relevant": 0},
    {"title": "Wärmepumpenbranche kritisiert fehlende Planungssicherheit", "relevant": 0},
    # === CLEARLY RELEVANT (true positives) ===
    {"title": "Sozialversicherung - Milliarden-Defizit in der Pflege", "relevant": 1},
    {"title": "Horst Zinsheimer über sein jahrzehntelanges Engagement für das DRK", "relevant": 1},
    {"title": "Warum ist Pflege in Schleswig-Holstein so teuer?", "relevant": 1},
    {"title": "Pflege auf dem Hof neu organisiert: Eine Bäuerin findet ihren Weg", "relevant": 1},
    {"title": "Pflege-Ausgaben steigen weiter", "relevant": 1},
    {"title": "Millionen-Minus erwartet: GKV-Chef: Bei der Pflegeversicherung brennt die Hütte", "relevant": 1},
    {"title": "Mehr Schutz für Kinder: Wie weit sind Kasseler Schulen mit dem Konzept?", "relevant": 1},
    {"title": "Pflege-Personal dringend gesucht: Warum Azubis sich für den Job entscheiden", "relevant": 1},
    {"title": "Pflegeversicherung 2025 leicht im Plus - Defizit knapp abgewendet", "relevant": 1},
    {"title": "Ohne BAföG automatisch Bürgergeld? Warum ein Teilzeitstudium wegen Pflege nicht ausreicht", "relevant": 1},
    {"title": "Einigung zum Frankfurter Haushalt dürfte vor allem Kita-Eltern freuen", "relevant": 1},
    {"title": "LWV Hessen Integrationsamt fördert Gut Halbersdorf mit rund 238.000 Euro", "relevant": 1},
    {"title": "Marktgemeinde will Pilotkommune für Hessen-Kita werden", "relevant": 1},
    {"title": "Mindestlohn statt Bürgergeld: Studie zeigt deutlichen Einkommensvorteil", "relevant": 1},
    {"title": "Equal Pay Day: Lohnlücke in Hessen größer als im Bundesschnitt", "relevant": 1},
    {"title": "Darmstädter AfD-Kandidatin verspottet ertrunkene Geflüchtete", "relevant": 1},
    {"title": "Migrationspolitische Bewährungsprobe für die einzige demokratische Mehrheit", "relevant": 1},
    {"title": "Zahlen zum Arbeitsmarkt: Jetzt gehen die Babyboomer wirklich in Rente", "relevant": 1},
    {"title": "Festung EU: Grundsteinlegung für Asyllager", "relevant": 1},
    {"title": "Lana-Doreen Paul ist die erste Absolventin im kleinsten Seniorenheim Deutschlands", "relevant": 1},
    {"title": "Bodo im März: Geht Pflege fair?", "relevant": 1},
]

SYSTEM_PROMPT = """Du bist ein Schnellfilter für die Liga der Freien Wohlfahrtspflege Hessen.

Entscheide NUR anhand des Titels: Ist dieser Nachrichtenartikel relevant für Sozialwohlfahrt?

RELEVANT: Pflege, Kinder/Jugend, Senioren, Migration/Flucht, Behinderung, Armut, Sozialversicherung, Gesundheitsversorgung, Tarifpolitik Sozialberufe, Ehrenamt, Wohlfahrtsverbände, Sozialpolitik.

IRRELEVANT: Reine Parteipolitik/Wahlen ohne Sozialbezug, Unterhaltung, Rätsel, Auslandspolitik, Energie/Klima ohne Sozialbezug, Sport, Satire.

Antworte NUR mit einem JSON: {"relevant": true} oder {"relevant": false}
Keine Erklärung. Kein Denken. Nur JSON."""


def call_ollama(model: str, title: str) -> tuple[bool | None, float]:
    """Call Ollama and return (relevant, duration_seconds)."""
    start = time.time()
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": title},
                ],
                "stream": False,
                "options": {
                    "temperature": 0,
                    "num_predict": 30,  # Very short response needed
                },
                "think": False,
            },
            timeout=30,
        )
        duration = time.time() - start
        content = resp.json()["message"]["content"].strip()

        # Parse response - look for true/false in JSON
        content_lower = content.lower()
        if '"relevant": true' in content_lower or '"relevant":true' in content_lower:
            return True, duration
        elif '"relevant": false' in content_lower or '"relevant":false' in content_lower:
            return False, duration
        elif "true" in content_lower and "false" not in content_lower:
            return True, duration
        elif "false" in content_lower and "true" not in content_lower:
            return False, duration
        else:
            print(f"    PARSE ERROR: {content[:80]}")
            return None, duration

    except Exception as e:
        duration = time.time() - start
        print(f"    ERROR: {e}")
        return None, duration


def run_benchmark(model: str) -> dict:
    """Run all test items through a model and collect results."""
    print(f"\n{'='*70}")
    print(f"MODEL: {model}")
    print(f"{'='*70}")

    # Warm up - load model into VRAM
    print("Warming up...", end="", flush=True)
    call_ollama(model, "Test")
    print(" done.")

    results = []
    total_time = 0
    correct = 0
    errors = 0
    fn = 0  # false negatives (relevant marked as irrelevant)
    fp = 0  # false positives (irrelevant marked as relevant)

    for i, item in enumerate(TEST_ITEMS):
        predicted, duration = call_ollama(model, item["title"])
        total_time += duration

        if predicted is None:
            errors += 1
            symbol = "?"
        elif predicted == bool(item["relevant"]):
            correct += 1
            symbol = "."
        elif item["relevant"] == 1 and not predicted:
            fn += 1
            symbol = "FN"
        else:
            fp += 1
            symbol = "FP"

        results.append({
            "title": item["title"],
            "expected": bool(item["relevant"]),
            "predicted": predicted,
            "duration": duration,
            "correct": predicted == bool(item["relevant"]) if predicted is not None else None,
        })

        # Progress indicator
        if symbol in ("FN", "FP"):
            label = "RELEVANT" if item["relevant"] else "IRRELEVANT"
            print(f"  {symbol} [{duration:.2f}s] ({label}) {item['title'][:70]}")
        else:
            print(f"  {symbol}  [{duration:.2f}s] {item['title'][:50]}", end="\r" if symbol == "." else "\n")

    valid = len(TEST_ITEMS) - errors
    n_relevant = sum(1 for i in TEST_ITEMS if i["relevant"])
    n_irrelevant = len(TEST_ITEMS) - n_relevant

    avg_time = total_time / len(TEST_ITEMS)
    accuracy = correct / valid if valid > 0 else 0
    recall = (n_relevant - fn) / n_relevant if n_relevant > 0 else 0
    precision_neg = (n_irrelevant - fp) / n_irrelevant if n_irrelevant > 0 else 0

    print(f"\n--- {model} Results ---")
    print(f"  Accuracy:     {accuracy:.1%} ({correct}/{valid})")
    print(f"  FN (missed):  {fn}/{n_relevant} relevant items wrongly rejected")
    print(f"  FP (leaked):  {fp}/{n_irrelevant} irrelevant items still passed")
    print(f"  Parse errors: {errors}")
    print(f"  Avg time:     {avg_time:.2f}s per item")
    print(f"  Total time:   {total_time:.1f}s for {len(TEST_ITEMS)} items")
    print(f"  Throughput:   {len(TEST_ITEMS)/total_time:.1f} items/sec")

    return {
        "model": model,
        "accuracy": accuracy,
        "fn": fn,
        "fp": fp,
        "errors": errors,
        "avg_time_s": avg_time,
        "total_time_s": total_time,
        "throughput": len(TEST_ITEMS) / total_time,
        "recall": recall,
        "irrelevant_rejection_rate": precision_neg,
    }


def main():
    print("=" * 70)
    print("Mini-Model Benchmark: Title-Only Relevance Pre-Filter")
    print(f"Test set: {len(TEST_ITEMS)} items "
          f"({sum(1 for i in TEST_ITEMS if i['relevant'])} relevant, "
          f"{sum(1 for i in TEST_ITEMS if not i['relevant'])} irrelevant)")
    print("=" * 70)

    # Unload any loaded model first
    requests.post(f"{OLLAMA_URL}/api/generate",
                  json={"model": "qwen3:30b", "keep_alive": "0s"},
                  timeout=5)

    summaries = []
    for model in MODELS:
        summary = run_benchmark(model)
        summaries.append(summary)

        # Unload model after benchmark
        requests.post(f"{OLLAMA_URL}/api/generate",
                      json={"model": model, "keep_alive": "0s"},
                      timeout=5)

    # Final comparison
    print("\n" + "=" * 70)
    print("COMPARISON")
    print("=" * 70)
    print(f"{'Model':<15} {'Acc':>6} {'FN':>4} {'FP':>4} {'Recall':>7} {'Reject%':>8} {'Avg(s)':>7} {'Items/s':>8}")
    print("-" * 70)
    for s in summaries:
        print(f"{s['model']:<15} {s['accuracy']:>5.1%} {s['fn']:>4} {s['fp']:>4} "
              f"{s['recall']:>6.1%} {s['irrelevant_rejection_rate']:>7.1%} "
              f"{s['avg_time_s']:>6.2f}s {s['throughput']:>7.1f}")

    print("\nKey metrics for pre-filter viability:")
    print("  - Recall must stay >95% (we can't lose relevant items)")
    print("  - Rejection rate shows how many FP the mini-model catches")
    print("  - Speed must be <1s/item to be worthwhile vs waiting for LLM")


if __name__ == "__main__":
    main()
