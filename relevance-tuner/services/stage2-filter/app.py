"""Stage-2 junk filter: distilled gbert-base student scored on gpu1, called by the backend LLM worker right before the LLM.

Scores one item at a time (requests are serialised) and returns keep/skip:
  ensemble mode (request carries the stage-1 M1 score of the matching model id): z(m1) + z(student) < ensemble_threshold -> skip
  student mode (no / foreign M1 score):                                           student < student_threshold          -> skip
Thresholds and z-score parameters: thresholds.json (built by experiments/retrain_2026_11_23/cascade_eval.py).
The backend fails open on any error, so this service only ever removes items when it answers.

Env: MODEL_DIR (weights, volume-mounted, not in git), THRESHOLDS, DEVICE (cuda|cpu), PRECISION (autocast|fp16|fp32), MAX_LEN (512).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("stage2")

MODEL_DIR = os.environ.get("MODEL_DIR", "/models/gbert512_s0")
THRESHOLDS = os.environ.get("THRESHOLDS", "/app/thresholds.json")
DEVICE = os.environ.get("DEVICE", "cuda")
PRECISION = os.environ.get("PRECISION", "autocast")
MAX_LEN = int(os.environ.get("MAX_LEN", "512"))
TITLE_TOKENS = 64        # as in training (tune_student.py)
CONTENT_CHARS = 6000     # training data cut content at 6,000 chars

state: dict = {}


def decide(student: float, m1_score: float | None, m1_model: str | None, thr: dict) -> dict:
    """Pure decision rule (no torch)."""
    if m1_score is not None and m1_model == thr["m1_model_id"]:
        score = (m1_score - thr["m1_oof_mean"]) / thr["m1_oof_std"] + (student - thr["student_oof_mean"]) / thr["student_oof_std"]
        mode, threshold = "ensemble", thr["ensemble_threshold"]
    else:
        score, mode, threshold = student, "student", thr["student_threshold"]
    return {"mode": mode, "score": float(score), "threshold": float(threshold), "keep": bool(score >= threshold)}


def encode(tok, title: str, content: str) -> list[int]:
    """Exactly the training encoding: [CLS] title[:64 tok] [SEP] content (head-truncated) [SEP]."""
    t = tok(title or "", add_special_tokens=False)["input_ids"][:TITLE_TOKENS]
    c = tok((content or "")[:CONTENT_CHARS], add_special_tokens=False)["input_ids"]
    return [tok.cls_token_id] + t + [tok.sep_token_id] + c[: MAX_LEN - len(t) - 3] + [tok.sep_token_id]


def _score_sync(title: str, content: str) -> tuple[float, int]:
    import torch

    tok, model = state["tok"], state["model"]
    ids = encode(tok, title, content)
    ii = torch.tensor([ids], device=DEVICE)
    am = torch.ones_like(ii)
    with torch.inference_mode():
        if PRECISION == "autocast" and DEVICE == "cuda":
            with torch.autocast("cuda", dtype=torch.float16):
                out = model(input_ids=ii, attention_mask=am).logits.float()[0, 0]
        else:
            out = model(input_ids=ii, attention_mask=am).logits.float()[0, 0]
    return float(out.item()), len(ids)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import torch
    from transformers import BertForSequenceClassification, BertTokenizerFast

    state["thr"] = json.load(open(THRESHOLDS))
    state["tok"] = BertTokenizerFast.from_pretrained(MODEL_DIR)
    model = BertForSequenceClassification.from_pretrained(MODEL_DIR, num_labels=1)
    if PRECISION == "fp16" and DEVICE == "cuda":
        model = model.half()
    state["model"] = model.to(DEVICE).eval()
    state["lock"] = asyncio.Lock()
    state["stats"] = {"scored": 0, "skipped": 0, "errors": 0}
    _score_sync("warmup", "warmup")
    vram = torch.cuda.memory_reserved() / 2**20 if DEVICE == "cuda" else 0
    logger.info(f"stage2 ready: {MODEL_DIR} device={DEVICE} precision={PRECISION} reserved_vram_mb={vram:.0f}")
    yield
    state.clear()


app = FastAPI(title="Liga stage-2 junk filter", lifespan=lifespan)


class ScoreRequest(BaseModel):
    title: str = ""
    content: str = Field(default="", max_length=200000)
    m1_score: float | None = None   # stage-1 raw score (pre_filter.relevance_raw_score)
    m1_model: str | None = None     # stage-1 gate model id (pre_filter.gate_model)


class ScoreResponse(BaseModel):
    keep: bool
    mode: str
    score: float
    threshold: float
    student_score: float
    model: str
    tokens: int
    ms: float


@app.post("/score", response_model=ScoreResponse)
async def score(req: ScoreRequest):
    if "model" not in state:
        raise HTTPException(503, "model not loaded")
    t0 = time.perf_counter()
    try:
        async with state["lock"]:
            student, n = await asyncio.to_thread(_score_sync, req.title, req.content)
    except Exception as e:
        state["stats"]["errors"] += 1
        logger.exception("scoring failed")
        raise HTTPException(500, f"scoring failed: {e}")
    d = decide(student, req.m1_score, req.m1_model, state["thr"])
    state["stats"]["scored"] += 1
    state["stats"]["skipped"] += 0 if d["keep"] else 1
    return ScoreResponse(**d, student_score=student, model=state["thr"]["student_model"], tokens=n, ms=(time.perf_counter() - t0) * 1000)


@app.get("/health")
async def health():
    out = {"status": "ok" if "model" in state else "loading", "device": DEVICE, "precision": PRECISION, "model_dir": MODEL_DIR}
    if "thr" in state:
        out |= {"m1_model_id": state["thr"]["m1_model_id"], "ensemble_threshold": state["thr"]["ensemble_threshold"],
                "student_threshold": state["thr"]["student_threshold"], "stats": state["stats"]}
    if DEVICE == "cuda" and "model" in state:
        import torch
        out["vram_reserved_mb"] = round(torch.cuda.memory_reserved() / 2**20)
        out["vram_allocated_mb"] = round(torch.cuda.memory_allocated() / 2**20)
    return out
