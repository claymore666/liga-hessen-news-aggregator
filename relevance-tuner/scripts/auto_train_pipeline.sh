#!/bin/bash
# Auto-training pipeline - runs after relabeling completes

set -e
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner
source venv/bin/activate

LOG="/tmp/auto_train_pipeline.log"
exec > >(tee -a "$LOG") 2>&1

echo "========================================"
echo "Auto-training pipeline started at $(date)"
echo "========================================"

# Step 1: Wait for relabeling to complete
echo "[1/5] Waiting for relabeling to complete..."
RELABEL_PID=$(cat /tmp/relabeling.pid 2>/dev/null || echo "")
if [ -n "$RELABEL_PID" ] && kill -0 "$RELABEL_PID" 2>/dev/null; then
    echo "Relabeling PID: $RELABEL_PID - waiting..."
    while kill -0 "$RELABEL_PID" 2>/dev/null; do
        sleep 60
        tail -1 /tmp/relabeling.log 2>/dev/null | grep -oP '\d+\.\d+%.*ETA.*' || true
    done
fi
echo "Relabeling complete!"

# Step 2: Create train/val/test splits
echo ""
echo "[2/5] Creating train/val/test splits..."
python scripts/create_splits.py
echo "Splits created!"

# Step 3: Train model
echo ""
echo "[3/5] Training model (this will take ~30-45 min)..."
python train_qwen3.py
echo "Training complete!"

# Step 4: Convert to GGUF
echo ""
echo "[4/5] Converting to GGUF..."
cd models/qwen3-trained
if [ -f "convert_to_gguf.sh" ]; then
    bash convert_to_gguf.sh
else
    # Manual GGUF conversion
    python -c "
from unsloth import FastLanguageModel
model, tokenizer = FastLanguageModel.from_pretrained('.')
model.save_pretrained_gguf('gguf', tokenizer, quantization_method='q4_k_m')
"
fi
echo "GGUF conversion complete!"

# Step 5: Deploy to Ollama
echo ""
echo "[5/5] Deploying to Ollama..."
cd gguf
ollama create liga-relevance -f Modelfile
echo "Deployed to Ollama!"

echo ""
echo "========================================"
echo "Pipeline complete at $(date)"
echo "========================================"
echo ""
echo "Next step: Reprocess live items with:"
echo "  curl -X POST 'http://localhost:8000/api/items/reprocess?force=true'"
