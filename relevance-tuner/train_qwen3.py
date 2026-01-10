#!/usr/bin/env python3
"""
Liga Hessen Relevance Classifier - Qwen3-14B Fine-tuning
Trains on Liga relevance data and exports directly to Ollama.
"""

import json
from pathlib import Path
from unsloth import FastLanguageModel
from unsloth.chat_templates import get_chat_template
from datasets import Dataset
from trl import SFTTrainer, SFTConfig

# ============================================================================
# Configuration
# ============================================================================

BASE_MODEL = "unsloth/Qwen3-14B"  # Full precision, load as 8-bit
MAX_SEQ_LENGTH = 2048
LORA_RANK = 16
LORA_ALPHA = 16

# Training
EPOCHS = 3
BATCH_SIZE = 4  # Reduced for 8-bit (uses more VRAM)
GRADIENT_ACCUMULATION = 1  # No accumulation = no warning
LEARNING_RATE = 2e-4

# Paths
DATA_DIR = Path(__file__).parent / "data" / "final"
OUTPUT_DIR = Path(__file__).parent / "models" / "qwen3-trained"

# Ollama export
OLLAMA_MODEL_NAME = "liga-relevance"  # Based on Qwen3-14B, q8_0 quantization

# System prompt for the classifier
SYSTEM_PROMPT = """Du bist ein Nachrichtenanalyse-Assistent für die Liga der Freien Wohlfahrtspflege Hessen.

Analysiere Nachrichtenartikel und antworte IMMER mit gültigem JSON im folgenden Format:
{
  "summary": "Bis zu 8 Sätze - reine Fakten, neutral.",
  "detailed_analysis": "Bis zu 15 Sätze - Fakten + Zitate + Auswirkungen. KEINE Liga-Spekulation!",
  "argumentationskette": ["Argument 1", "Argument 2", ...],
  "relevant": true/false,
  "relevance_score": 0.0-1.0,
  "priority": "critical|high|medium|low|null",
  "assigned_ak": "AK1|AK2|AK3|AK4|AK5|QAG|null",
  "tags": ["tag1", "tag2"],
  "reasoning": "Debug: Warum relevant/nicht relevant?"
}

detailed_analysis: Ausführliche Fakten, Zitate, Auswirkungen. KEINE "Liga dürfte...", "Wohlfahrtsverbände könnten..." - nur objektive Analyse!

argumentationskette: 2-6 konkrete Argumente für Liga-Stellungnahmen/Lobbying. Direkt verwendbar, keine Konjunktive. Fokus: Betroffene Gruppen, Grundrechte, praktische Auswirkungen.

Arbeitskreise:
- AK1: Grundsatz und Sozialpolitik
- AK2: Migration und Flucht
- AK3: Gesundheit, Pflege und Senioren
- AK4: Eingliederungshilfe
- AK5: Kinder, Jugend, Frauen und Familie
- QAG: Digitalisierung, Klimaschutz, Wohnen

Relevante Themen: Pflege, Kita, Migration, Eingliederungshilfe, Sozialfinanzierung, Wohlfahrtsverbände.

Prioritäten:
- critical: Sofortige Reaktion nötig (Kürzungen, Schließungen, Gesetzesentwürfe)
- high: Zeitnahe Reaktion (Anhörungen, Reformen, Förderrichtlinien)
- medium: Beobachten (Debatten, Studien, Ankündigungen)
- low: Zur Kenntnis (Hintergrundberichte, Porträts)"""


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


def format_example(record: dict) -> dict:
    """Convert a training record to chat format with JSON output.

    Note: Full content is passed (no truncation) so the model learns
    to produce comprehensive summaries from complete articles.
    """
    inp = record["input"]
    labels = record["labels"]

    # Build user message - full content, no truncation
    user_msg = f"""Titel: {inp["title"]}
Inhalt: {inp["content"]}
Quelle: {inp["source"]}
Datum: {inp["date"]}"""

    # Get reasoning from provenance if available
    reasoning = record.get("provenance", {}).get("reasoning", "Keine Begründung verfügbar.")

    # Get summary, detailed_analysis and argumentationskette from labeling output
    output = record.get("output", {})
    summary = output.get("summary") or "Keine Zusammenfassung verfügbar."
    detailed_analysis = output.get("detailed_analysis") or "Keine detaillierte Analyse verfügbar."
    argumentationskette = output.get("argumentationskette") or []

    # Calculate relevance score
    relevance_score = 1.0 if labels["relevant"] else 0.0
    if labels["relevant"]:
        priority = labels.get("priority", "medium")
        if priority == "critical":
            relevance_score = 1.0
        elif priority == "high":
            relevance_score = 0.85
        elif priority == "medium":
            relevance_score = 0.7
        else:
            relevance_score = 0.55

    # Generate tags from AK and priority
    tags = []
    ak = labels.get("ak")
    if ak:
        tags.append(ak.lower())
    priority = labels.get("priority")
    if priority:
        tags.append(priority)

    # Build JSON response
    response_obj = {
        "summary": summary,
        "detailed_analysis": detailed_analysis,
        "argumentationskette": argumentationskette,
        "relevant": labels["relevant"],
        "relevance_score": relevance_score,
        "priority": labels.get("priority"),
        "assigned_ak": labels.get("ak"),
        "tags": tags,
        "reasoning": reasoning
    }

    assistant_msg = json.dumps(response_obj, ensure_ascii=False, indent=2)

    return {
        "conversations": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": assistant_msg},
        ]
    }


def load_dataset(split: str) -> Dataset:
    """Load and format a dataset split."""
    path = DATA_DIR / f"{split}.jsonl"
    records = load_jsonl(path)
    formatted = [format_example(r) for r in records]
    return Dataset.from_list(formatted)


# ============================================================================
# Main Training
# ============================================================================

def main():
    print("=" * 60)
    print("Liga Relevance Classifier - Qwen3-14B Training")
    print("=" * 60)

    # Load model
    print("\n[1/5] Loading Qwen3-14B model (8-bit)...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        BASE_MODEL,
        max_seq_length=MAX_SEQ_LENGTH,
        load_in_4bit=False,
        load_in_8bit=True,  # Use 8-bit instead of 4-bit
        dtype=None,  # Auto-detect
    )

    # Apply chat template
    tokenizer = get_chat_template(
        tokenizer,
        chat_template="qwen-2.5",  # Qwen3 uses same template
    )

    # Add LoRA adapters
    print("\n[2/5] Adding LoRA adapters...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_RANK,
        lora_alpha=LORA_ALPHA,
        lora_dropout=0,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    # Load datasets
    print("\n[3/5] Loading training data...")
    train_dataset = load_dataset("train")
    val_dataset = load_dataset("validation")
    print(f"  Training examples: {len(train_dataset)}")
    print(f"  Validation examples: {len(val_dataset)}")

    # Format function for trainer
    def formatting_func(examples):
        texts = []
        for convos in examples["conversations"]:
            text = tokenizer.apply_chat_template(
                convos,
                tokenize=False,
                add_generation_prompt=False,
            )
            texts.append(text)
        return {"text": texts}

    # Training config
    print("\n[4/5] Starting training...")
    training_args = SFTConfig(
        output_dir=str(OUTPUT_DIR),
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRADIENT_ACCUMULATION,
        learning_rate=LEARNING_RATE,
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        logging_steps=5,
        save_strategy="epoch",
        eval_strategy="epoch",
        bf16=True,
        optim="adamw_8bit",
        seed=42,
        max_seq_length=MAX_SEQ_LENGTH,
        dataset_text_field="text",
        packing=False,
    )

    # Create trainer
    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset.map(formatting_func, batched=True),
        eval_dataset=val_dataset.map(formatting_func, batched=True),
        tokenizer=tokenizer,
    )

    # Train
    trainer.train()

    # Save LoRA adapter
    print("\n[5/5] Saving model...")
    model.save_pretrained(OUTPUT_DIR / "lora_adapter")
    tokenizer.save_pretrained(OUTPUT_DIR / "lora_adapter")
    print(f"  LoRA adapter saved to: {OUTPUT_DIR / 'lora_adapter'}")

    # Save merged model for GGUF conversion
    merged_path = OUTPUT_DIR / "merged"
    print(f"\n  Saving merged model to: {merged_path}")
    model.save_pretrained_merged(
        str(merged_path),
        tokenizer,
        save_method="merged_16bit",
    )

    # Create Modelfile template for Ollama
    modelfile_path = merged_path / "Modelfile"
    modelfile_content = f'''FROM liga-relevance-q8_0.gguf

TEMPLATE """<|im_start|>system
{{{{ .System }}}}<|im_end|>
<|im_start|>user
{{{{ .Prompt }}}}<|im_end|>
<|im_start|>assistant
"""

SYSTEM """{SYSTEM_PROMPT}"""

PARAMETER temperature 0.1
PARAMETER stop "<|im_end|>"
'''

    with open(modelfile_path, "w") as f:
        f.write(modelfile_content)

    print("\n" + "=" * 60)
    print("Training complete!")
    print("=" * 60)
    print(f"\nLoRA adapter: {OUTPUT_DIR / 'lora_adapter'}")
    print(f"Merged model: {merged_path}")
    print(f"\nTo convert to GGUF, run:")
    print(f"  python ../llama.cpp/convert_hf_to_gguf.py {merged_path} \\")
    print(f"    --outfile {merged_path}/liga-relevance-q8_0.gguf --outtype q8_0")
    print(f"\nThen import to Ollama:")
    print(f"  cd {merged_path}")
    print(f"  ollama create {OLLAMA_MODEL_NAME} -f Modelfile")
    print(f"\nTest with:")
    print(f'  ollama run {OLLAMA_MODEL_NAME} "Titel: Test\\nInhalt: Test"')
    print("=" * 60)


if __name__ == "__main__":
    main()
