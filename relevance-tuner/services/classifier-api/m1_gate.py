"""M1 relevance gate: 3-seed MLP (805-256-1, ReLU) on nomic embedding (768) + 37 standardised features.

Serving is numpy only (no torch in this container). Weights and the level mapping come from
models/relevance_mlp_m1.npz + .json, exported by
relevance-tuner/experiments/retrain_2026_11_23/m1_export.py (training recipe: tune_mlp.py, variant M1).

score      = mean logit of the 3 seeds
confidence = sigmoid(logit(0.25) + k * (score - t1)), monotone, so that
             score == t1 (own threshold, 98% recall on labelled rows) -> 0.25 (edge-case boundary)
             score == t2 (matches today's medium/edge split)           -> 0.50 (medium boundary)
The backend's 0.25 / 0.5 semantics stay unchanged.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

DEFAULT_PATH = "models/relevance_mlp_m1.npz"


class M1Gate:
    def __init__(self, weights: dict[str, np.ndarray], meta: dict):
        self.meta = meta
        self.model_id: str = meta["model_id"]
        self.t1 = float(meta["t1"])
        self.t2 = float(meta["t2"])
        self.k = float(meta["k"])
        self.relevant_threshold = float(meta.get("relevant_threshold", 0.5))
        self.feature_mean = np.asarray(weights["feature_mean"], dtype=np.float64)
        self.feature_scale = np.asarray(weights["feature_scale"], dtype=np.float64)
        self.layers = []
        i = 0
        while f"w1_{i}" in weights:
            self.layers.append((
                np.asarray(weights[f"w1_{i}"], dtype=np.float32),
                np.asarray(weights[f"b1_{i}"], dtype=np.float32),
                np.asarray(weights[f"w2_{i}"], dtype=np.float32),
                float(np.asarray(weights[f"b2_{i}"]).reshape(-1)[0]),
            ))
            i += 1
        if not self.layers:
            raise ValueError("M1 weights contain no seeds")
        self.input_dim = self.layers[0][0].shape[0]

    @classmethod
    def load(cls, path: str = DEFAULT_PATH) -> "M1Gate":
        p = Path(path)
        with np.load(p) as z:
            weights = {k: z[k] for k in z.files}
        meta = json.loads(p.with_suffix(".json").read_text())
        return cls(weights, meta)

    def score(self, embedding, features) -> np.ndarray:
        """Mean logit for a batch. embedding: (n, 768), features: raw (unscaled) (n, 37)."""
        e = np.atleast_2d(np.asarray(embedding, dtype=np.float32))
        f = np.atleast_2d(np.asarray(features, dtype=np.float64))
        x = np.hstack([e, (f - self.feature_mean) / self.feature_scale]).astype(np.float32)
        if x.shape[1] != self.input_dim:
            raise ValueError(f"M1 expects {self.input_dim} inputs, got {x.shape[1]}")
        out = np.zeros(x.shape[0], dtype=np.float64)
        for w1, b1, w2, b2 in self.layers:
            out += np.maximum(x @ w1 + b1, 0.0) @ w2 + b2
        return out / len(self.layers)

    def confidence(self, score: float) -> float:
        z = math.log(1 / 3) + self.k * (float(score) - self.t1)
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)
