#!/bin/bash
# Full end-to-end retraining pipeline
# Usage: ./scripts/full_retrain.sh [--model qwen3:32b]

set -e
cd /home/kamienc/claude.ai/relevance-tuner/relevance-tuner
source venv/bin/activate

MODEL="${2:-qwen3:32b}"
LOG_DIR="/tmp/retrain_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"

echo "========================================"
echo "Full Retraining Pipeline"
echo "Started: $(date)"
echo "Model: $MODEL"
echo "Logs: $LOG_DIR"
echo "========================================"

# Step 0: Prerequisites
echo ""
echo "[0/7] Preparing environment..."

# Stop backend to free GPU
echo "  Stopping news-aggregator backend..."
cd /home/kamienc/claude.ai/ligahessen/news-aggregator
docker compose stop backend 2>/dev/null || true
cd /home/kamienc/claude.ai/relevance-tuner/relevance-tuner

# Unload Ollama models
echo "  Unloading Ollama models..."
ollama stop liga-relevance 2>/dev/null || true
ollama stop qwen3:32b 2>/dev/null || true
ollama stop qwen3:14b 2>/dev/null || true
sleep 5

# Check GPU
GPU_MEM=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | tr -d ' ')
echo "  GPU memory used: ${GPU_MEM} MiB"
if [ "$GPU_MEM" -gt 2000 ]; then
    echo "  WARNING: GPU memory still high. Waiting 30s..."
    sleep 30
fi

# Step 1: Relabel training data
echo ""
echo "[1/7] Relabeling training data with $MODEL..."
echo "  This takes ~3 hours for 1000 items"
python scripts/label_with_ollama.py --all --model "$MODEL" 2>&1 | tee "$LOG_DIR/relabeling.log"
echo "  Relabeling complete!"

# Unload labeling model to free GPU for training
echo "  Unloading $MODEL..."
ollama stop "$MODEL" 2>/dev/null || true
sleep 10

# Step 2: Create splits
echo ""
echo "[2/7] Creating train/val/test splits..."
python scripts/create_splits.py 2>&1 | tee "$LOG_DIR/splits.log"
echo "  Splits created!"

# Step 3: Train model
echo ""
echo "[3/7] Training model (~45 min)..."
python train_qwen3.py 2>&1 | tee "$LOG_DIR/training.log"
echo "  Training complete!"

# Step 4: Convert to GGUF
echo ""
echo "[4/7] Converting to GGUF..."
cd models/qwen3-trained
python -c "
from unsloth import FastLanguageModel
print('Loading model...')
model, tokenizer = FastLanguageModel.from_pretrained('.')
print('Converting to GGUF (q8_0)...')
model.save_pretrained_gguf('gguf', tokenizer, quantization_method='q8_0')
print('Done!')
" 2>&1 | tee "$LOG_DIR/gguf.log"
cd /home/kamienc/claude.ai/relevance-tuner/relevance-tuner
echo "  GGUF conversion complete!"

# Step 5: Deploy to Ollama
echo ""
echo "[5/7] Deploying to Ollama..."
cd models/qwen3-trained/gguf
ollama create liga-relevance -f Modelfile 2>&1 | tee "$LOG_DIR/ollama.log"
cd /home/kamienc/claude.ai/relevance-tuner/relevance-tuner
echo "  Deployed to Ollama!"

# Step 6: Start backend
echo ""
echo "[6/7] Starting news-aggregator backend..."
cd /home/kamienc/claude.ai/ligahessen/news-aggregator
docker compose up -d backend
sleep 15
cd /home/kamienc/claude.ai/relevance-tuner/relevance-tuner
echo "  Backend started!"

# Step 7: Reprocess items
echo ""
echo "[7/7] Reprocessing live items..."
echo "  This runs in background (~1.5 hours for 1600 items)"
curl -X POST "http://localhost:8000/api/items/reprocess?force=true" 2>&1 | tee "$LOG_DIR/reprocess.log"
echo "  Reprocessing started!"

echo ""
echo "========================================"
echo "Pipeline Complete!"
echo "Finished: $(date)"
echo "Logs: $LOG_DIR"
echo "========================================"
echo ""
echo "Monitor reprocessing with:"
echo "  docker logs -f liga-news-backend"
