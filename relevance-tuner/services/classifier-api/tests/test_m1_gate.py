"""Unit tests for the M1 relevance gate (no running service, no Ollama).

Run: pytest -m "not integration" tests/test_m1_gate.py
"""

import asyncio
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m1_gate import M1Gate

MODELS = Path(__file__).resolve().parents[1] / "models"


@pytest.fixture(scope="module")
def gate():
    return M1Gate.load(str(MODELS / "relevance_mlp_m1.npz"))


def test_parity_with_training_code(gate):
    """numpy serving == torch forward of the trained modules (fixture exported by m1_export.py, synthetic inputs)."""
    with np.load(MODELS / "relevance_mlp_m1_parity.npz") as z:
        emb, feat, expected = z["embedding"], z["features"], z["expected_score"]
    got = gate.score(emb, feat)
    max_abs = float(np.abs(got - expected).max())
    print(f"M1 parity max abs diff: {max_abs:.2e}")
    assert max_abs < 1e-4


def test_three_seeds_and_input_dim(gate):
    assert len(gate.layers) == 3
    assert gate.input_dim == 768 + 37


def test_level_mapping_anchors(gate):
    assert gate.confidence(gate.t1) == pytest.approx(0.25, abs=1e-12)
    assert gate.confidence(gate.t2) == pytest.approx(0.5, abs=1e-12)
    assert gate.t2 > gate.t1


def test_level_mapping_monotone_and_bounded(gate):
    xs = np.linspace(gate.t1 - 2000, gate.t2 + 2000, 4001)
    cs = [gate.confidence(x) for x in xs]
    assert all(0.0 <= c <= 1.0 for c in cs)
    assert all(b >= a for a, b in zip(cs, cs[1:]))
    # strictly below t1 -> below the edge-case boundary (skipped), between -> edge case, above t2 -> medium
    assert gate.confidence(gate.t1 - 1e-6) < 0.25
    assert 0.25 < gate.confidence((gate.t1 + gate.t2) / 2) < 0.5
    assert gate.confidence(gate.t2 + 1e-6) > 0.5


def test_metadata_documents_rules():
    meta = json.loads((MODELS / "relevance_mlp_m1.json").read_text())
    assert meta["conf_at_t1"] == 0.25 and meta["conf_at_t2"] == 0.5
    assert "98% recall" in meta["t1_rule"]
    assert math.isclose(meta["k"], math.log(3) / (meta["t2"] - meta["t1"]), rel_tol=1e-9)


class _FakeEmbedder:
    embedding_dim = 768

    def __init__(self, vec):
        self.vec = vec

    async def encode(self, texts, show_progress_bar=False):
        return [self.vec for _ in texts]


@pytest.mark.parametrize("gate_name", ["m1", "lr"])
def test_predict_contract(monkeypatch, gate_name):
    """/classify contract keeps its keys; m1 adds the raw score, lr leaves it None (rollback path)."""
    pytest.importorskip("sklearn")
    from classifier import EmbeddingClassifier

    monkeypatch.setenv("RELEVANCE_GATE", gate_name)
    monkeypatch.setenv("M1_MODEL_PATH", str(MODELS / "relevance_mlp_m1.npz"))
    clf = EmbeddingClassifier.load(str(MODELS / "embedding_classifier_nomic-v2.pkl"))
    rng = np.random.default_rng(1)
    v = rng.normal(0, 1, 768)
    clf.embedder = _FakeEmbedder((v / np.linalg.norm(v)).tolist())
    res = asyncio.run(clf.predict("Titel", "Inhalt über Kita-Finanzierung in Hessen", "Quelle"))
    for key in ("relevant", "relevance_confidence", "priority", "ak", "aks", "ak_confidences"):
        assert key in res
    assert 0.0 <= res["relevance_confidence"] <= 1.0
    if gate_name == "m1":
        assert res["gate_model"] == clf.m1.model_id
        assert res["relevance_confidence"] == pytest.approx(clf.m1.confidence(res["relevance_raw_score"]))
    else:
        assert clf.m1 is None and res["relevance_raw_score"] is None and res["gate_model"] == "lr"
