# Cloud LLM Cost Analysis

Cost comparison for running LLM inference on cloud providers as fallback when gpu1 is offline.

**Analysis Date**: 2026-01-19

## LLM-Only Cloud Scenario

The classifier and LLM have different characteristics that favor different deployment strategies:

| Component | Local Performance | Cloud Need |
|-----------|-------------------|------------|
| **Classifier** | ~100ms, minimal GPU | **None** - runs on classifier-api (gpu1) |
| **LLM** | ~16-20s, needs 24GB VRAM | **Yes** - fallback when gpu1 offline |

### Why Classifier Stays Local

- **Fast**: ~100ms per request, processes 617 items/day in ~1 minute total
- **Lightweight**: Uses nomic-embed-text-v2 + sklearn, runs fine on CPU or small GPU
- **Always available**: classifier-api runs on gpu1 which is typically online
- **Free**: No cloud cost, no network latency

### When LLM Needs Cloud Fallback

- gpu1 goes into sleep mode (auto-suspend after 1h idle)
- gpu1 needs maintenance or reboot
- Processing backlog during gpu1 downtime

**Result**: Only the LLM portion (~251 requests/day) needs cloud fallback, not the classifier.

## Workload Summary (Last 3 Days)

| Date | Total Items | Classifier Requests | LLM Requests | LLM Rate |
|------|-------------|---------------------|--------------|----------|
| 2026-01-16 | 516 | 516 | 247 | 47.9% |
| 2026-01-17 | 301 | 301 | 130 | 43.2% |
| 2026-01-18 | 1,034 | 1,034 | 375 | 36.3% |
| **Total** | **1,851** | **1,851** | **752** | **40.6%** |
| **Daily Avg** | **617** | **617** | **251** | - |

### Processing Pipeline

```
Item → Classifier (100%) → [confidence < 0.25?] → Skip LLM
                        → [confidence >= 0.25?] → LLM Analysis (~41%)
```

- **Classifier**: Every item (100%) - ~100ms per request
- **LLM**: Only items with relevance confidence >= 0.25 (~41%) - ~16.5s per request

## Processing Times (Measured)

| Component | Avg Time | Model |
|-----------|----------|-------|
| Classifier | 100ms | nomic-embed-text-v2 + sklearn |
| LLM | 16,500ms | qwen3:14b-q8_0 |

---

## Option 1: RunPod Serverless

RunPod serverless charges per-second of GPU compute time.

### Pricing (RTX 4090 / A100)

| GPU | Cost/sec | Min billing |
|-----|----------|-------------|
| RTX 4090 | $0.00044/sec | 5 sec |
| A100 80GB | $0.00140/sec | 5 sec |

### Cost Calculation

**Classifier (embedding model):**
- Requests per day: 617
- Time per request: 0.1s
- Min billing: 5s per cold start (assume 1 cold start per 10 requests)
- Effective time: 617 × 0.1s + 62 × 5s = 61.7s + 310s = 371.7s
- Daily cost (RTX 4090): 371.7 × $0.00044 = **$0.16**

**LLM (qwen3:14b):**
- Requests per day: 251
- Time per request: 16.5s
- Min billing: 5s per cold start (assume 1 cold start per 10 requests)
- Effective time: 251 × 16.5s + 25 × 5s = 4,141.5s + 125s = 4,266.5s = **71.1 min**
- Daily cost (RTX 4090): 4,266.5 × $0.00044 = **$1.88**

### RunPod Daily Cost Summary

| Component | Requests | Compute Time | Daily Cost |
|-----------|----------|--------------|------------|
| Classifier | 617 | ~6.2 min | $0.16 |
| LLM | 251 | ~71.1 min | $1.88 |
| **Total** | **868** | **~77.3 min** | **$2.04** |

### Last 3 Days Actual Cost (RunPod)

| Date | Items | Classifier | LLM | Total Cost |
|------|-------|------------|-----|------------|
| 2026-01-16 | 516 | $0.13 | $1.85 | $1.98 |
| 2026-01-17 | 301 | $0.08 | $0.97 | $1.05 |
| 2026-01-18 | 1,034 | $0.27 | $2.81 | $3.08 |
| **Total** | **1,851** | **$0.48** | **$5.63** | **$6.11** |

---

## Option 2: Vast.ai Hourly Rental (Recommended for LLM-only)

Vast.ai charges per-hour for GPU instances. For LLM-only fallback, an RTX 3090 at ~$0.25/hr is cost-effective.

### Pricing (LLM-only)

| GPU | Cost/hour | VRAM | qwen3:14b Speed |
|-----|-----------|------|-----------------|
| RTX 3090 | $0.20-0.30 | 24GB | ~15-20s/request |
| RTX 4090 | $0.35-0.50 | 24GB | ~12-16s/request |

**Recommendation**: RTX 3090 @ $0.25/hour - sufficient for qwen3:14b-q8_0

### Processing Capacity (LLM-only)

**RTX 3090 throughput:**
- LLM: 3,600s / 20s = **180 items/hour**
- Daily need: 251 items → 251 / 180 = **1.4 hours**

**With cold start + overhead:** ~1.5-2 hours/day

### Cold Start Considerations

| Phase | Time | Notes |
|-------|------|-------|
| Instance start (stopped) | ~5 sec | Pre-created instance |
| Model load (first request) | 30-60 sec | qwen3:14b into VRAM |
| Subsequent requests | ~20 sec | Model stays loaded |

### Vast.ai Daily Cost Summary (LLM-only)

| Component | Time | Rate | Daily Cost |
|-----------|------|------|------------|
| GPU (RTX 3090) | 1.5 hrs | $0.25/hr | $0.38 |
| Storage (20GB, 24h) | - | ~$0.01/day | $0.01 |
| **Total** | - | - | **~$0.40/day** |

### Last 3 Days Estimated Cost (Vast.ai LLM-only)

| Date | LLM Items | Compute Time | Hours Billed | Cost |
|------|-----------|--------------|--------------|------|
| 2026-01-16 | 247 | 82 min | 2 hrs | $0.50 |
| 2026-01-17 | 130 | 43 min | 1 hr | $0.25 |
| 2026-01-18 | 375 | 125 min | 3 hrs | $0.75 |
| **Total** | **752** | **250 min** | **6 hrs** | **$1.50** |

*Note: Rounded up to whole hours. Assumes 20s/request average on RTX 3090.*

---

## Cost Comparison Summary

### Daily Average Cost (LLM-only Fallback)

| Provider | Setup | Daily Cost | Monthly Est. |
|----------|-------|------------|--------------|
| **gpu1 (local)** | Free, always preferred | **$0.00** | **$0.00** |
| **Vast.ai RTX 3090** | LLM-only, $0.25/hr | **$0.40** | **$12.00** |
| **RunPod Serverless** | LLM-only, per-second | **$1.88** | **$56.40** |

*Note: Classifier always runs locally (free). Cloud costs are LLM-only.*

### Last 3 Days Total (LLM-only)

| Provider | 3-Day Cost | Avg/Day | Notes |
|----------|------------|---------|-------|
| gpu1 (local) | $0.00 | $0.00 | Actual usage |
| Vast.ai RTX 3090 | $1.50 | $0.50 | Estimated |
| RunPod Serverless | $5.63 | $1.88 | Estimated |

---

## Recommendations

### Primary: Local GPU (gpu1)
- **Cost**: Free
- **Latency**: Lowest (~16s for LLM)
- **Classifier**: Always local (100ms, free)
- **Availability**: Depends on gpu1 uptime

### Fallback: Vast.ai RTX 3090 (Recommended)
- **Best for**: LLM-only fallback when gpu1 is offline
- **Strategy**: Keep stopped instance, start on demand
- **Cost**: ~$0.40/day for average workload
- **Startup**: ~5 sec (stopped→running) + 30-60 sec (model load)

### Alternative: RunPod Serverless
- **Best for**: Real-time LLM processing, low latency
- **Strategy**: On-demand serverless inference
- **Cost**: ~$1.88/day (4.7x more expensive than Vast.ai)
- **Trade-off**: Higher cost, but instant availability

### Simple Strategy (Recommended)

```
Classifier: Always local (gpu1/classifier-api) - free, fast

LLM:
1. gpu1 available → Use local Ollama (free, ~16s)
2. gpu1 offline → Start Vast.ai RTX 3090 (~$0.25/hr, ~20s)
```

---

## Implementation Notes

### Architecture (LLM-only Cloud)

```
┌─────────────────────────────────────────────────────────────┐
│                        gpu1 (local)                         │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │ classifier   │    │   Ollama     │    │   Backend    │   │
│  │ (always on)  │    │ (when awake) │    │   (Docker)   │   │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘   │
│         │ 100ms             │ ~16s              │           │
└─────────┼───────────────────┼───────────────────┼───────────┘
          │                   │                   │
          │                   │ fallback          │
          │                   ▼                   │
          │         ┌─────────────────┐           │
          │         │  Vast.ai 3090   │           │
          │         │  (LLM only)     │           │
          │         │  ~20s/request   │           │
          │         └─────────────────┘           │
          │                                       │
          └───────────────────────────────────────┘
```

### Classifier Setup (Local Only)
- Runs on `classifier-api` container (gpu1:8082)
- Uses `nomic-embed-text-v2` + sklearn
- No cloud fallback needed - lightweight, always available

### LLM Queue Management
- Items needing LLM marked with `needs_llm_processing=true`
- Backend checks gpu1 Ollama first
- If unavailable, can route to Vast.ai endpoint
- `OLLAMA_BASE_URL` env var controls endpoint

---

## Vast.ai Practical Setup (LLM-only)

### Quick Start: RTX 3090 with Ollama

```bash
# Install CLI
pip install vastai

# Set API key (get from vast.ai/console/account)
vastai set api-key YOUR_API_KEY

# Search for RTX 3090 with CUDA 12+, sorted by price
vastai search offers 'gpu_name=RTX_3090 cuda_vers>=12.0 rentable=True' -o 'dph'

# Create instance with Ollama image (use offer ID from search)
# --disk 20 is enough for qwen3:14b-q8_0 (~9GB model)
vastai create instance OFFER_ID --image ollama/ollama --disk 20 --ssh

# Wait for instance to start, get instance ID
vastai show instances

# Get connection info (IP and ports)
vastai show instance INSTANCE_ID
```

### One-time Model Setup

```bash
# SSH into the instance
ssh -p PORT root@IP_ADDRESS

# Pull the model (takes 2-5 min depending on network)
ollama pull qwen3:14b-q8_0

# Verify model is loaded
ollama list

# Test inference
curl http://localhost:11434/api/generate -d '{
  "model": "qwen3:14b-q8_0",
  "prompt": "Hello",
  "stream": false
}'
```

### Connect Backend to Vast.ai

```bash
# Option 1: Direct connection (if port 11434 exposed)
export OLLAMA_BASE_URL=http://VAST_IP:11434

# Option 2: SSH tunnel (more secure, always works)
ssh -L 11434:localhost:11434 -p PORT root@IP_ADDRESS

# Then use localhost
export OLLAMA_BASE_URL=http://localhost:11434
```

### Instance Management

```bash
# Stop instance (preserves data, stops GPU billing)
vastai stop instance INSTANCE_ID

# Start instance (resume stopped instance, ~5 sec)
vastai start instance INSTANCE_ID

# Destroy instance (permanent, deletes data)
vastai destroy instance INSTANCE_ID
```

### Startup/Shutdown Times

| Operation | Time | Notes |
|-----------|------|-------|
| **Start existing instance** | **~1-5 seconds** | Instance already created, image cached |
| **Create new instance (cached image)** | **~30 seconds** | Docker image already on host |
| **Create new instance (uncached)** | **Minutes to hours** | Depends on image size + host internet |
| **Stop instance** | **~1-5 seconds** | Data preserved, GPU billing stops |
| **Destroy instance** | **Instant** | Irreversible |

### Billing Model

| State | GPU Billing | Storage Billing |
|-------|-------------|-----------------|
| Creating/Loading | No | Yes |
| Running | Yes | Yes |
| Stopped | No | Yes |
| Destroyed | No | No |

### Recommended Strategy: Keep Stopped Instance

Instead of creating/destroying instances hourly:

1. **Create once** with pre-cached Ollama image + models
2. **Stop** when idle (no GPU cost, small storage cost ~$0.02/day)
3. **Start** when queue > 0 (~5 second startup)
4. Process queue
5. **Stop** when done

**Daily cost with stopped RTX 3090 instance:**

| Component | Cost |
|-----------|------|
| GPU time (1.5 hrs/day @ $0.25/hr) | $0.38/day |
| Storage (20GB stopped) | ~$0.01/day |
| **Total** | **~$0.40/day** |

### Automation Script Example

```python
import vastai

# Check queue size
queue_size = get_unprocessed_count()

if queue_size > 0:
    # Start stopped instance
    vastai.start_instance(INSTANCE_ID)

    # Wait for running state
    wait_for_status(INSTANCE_ID, "running")

    # Process queue via Ollama API on instance
    process_queue(instance_ip)

    # Stop when done
    vastai.stop_instance(INSTANCE_ID)
```

### Pre-cached Docker Image (Optional)

Create a custom image with the LLM pre-loaded:

```dockerfile
FROM ollama/ollama

# Pre-pull LLM model during build
RUN ollama pull qwen3:14b-q8_0
```

This reduces cold start time from ~2-5 min (model download) to ~30-60 sec (model load).

**Note:** Classifier model (`nomic-embed-text`) not needed - runs locally on gpu1.

---

## Sources

- [Vast.ai CLI Commands](https://docs.vast.ai/cli/commands)
- [Vast.ai Instance Management](https://docs.vast.ai/documentation/instances/manage-instances)
- [Vast.ai API Reference](https://docs.vast.ai/api-reference/instances/create-instance)
