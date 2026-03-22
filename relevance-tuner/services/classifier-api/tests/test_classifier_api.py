"""Integration tests for the classifier API.

Requires a running classifier instance with real Ollama and ChromaDB data.
Configure target via CLASSIFIER_URL env var (default: http://localhost:8082).

Usage:
    cd relevance-tuner/services/classifier-api
    pytest tests/ -v
"""

import uuid

import httpx
import pytest

from .conftest import TEST_ID_PREFIX

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Read-only endpoint tests
# ---------------------------------------------------------------------------


class TestReadOnlyEndpoints:
    """Tests that don't modify any data."""

    def test_root(self, client: httpx.Client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "service" in data
        assert "endpoints" in data
        assert "indexes" in data

    def test_health(self, client: httpx.Client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["ollama_available"] is True
        assert data["search_index_items"] > 0
        assert data["duplicate_index_items"] > 0
        assert data["classifier_version"] is not None

    def test_ids(self, client: httpx.Client):
        resp = client.get("/ids")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] > 0
        assert len(data["ids"]) == data["count"]

        # Cross-check with health endpoint
        health = client.get("/health").json()
        assert data["count"] == health["search_index_items"]

    def test_storage(self, client: httpx.Client):
        resp = client.get("/storage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["search_index_size_bytes"] > 0
        assert data["duplicate_index_size_bytes"] > 0
        assert data["search_index_items"] > 0
        assert data["duplicate_index_items"] > 0

    def test_classify_relevant(self, client: httpx.Client):
        resp = client.post("/classify", json={
            "title": "Hessen plant neue Kita-Förderung für soziale Einrichtungen",
            "content": (
                "Die hessische Landesregierung hat ein neues Förderprogramm "
                "für Kindertagesstätten angekündigt. Das Sozialministerium "
                "stellt 50 Millionen Euro für den Ausbau der Betreuungsplätze "
                "in sozialen Brennpunkten bereit."
            ),
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["relevant"] is True
        assert data["priority"] in ("high", "medium", "low")
        assert len(data["aks"]) > 0
        assert data["relevance_confidence"] > 0

    def test_classify_irrelevant(self, client: httpx.Client):
        resp = client.post("/classify", json={
            "title": "FC Bayern gewinnt die Bundesliga",
            "content": (
                "Der FC Bayern München hat zum elften Mal in Folge die "
                "deutsche Fußball-Bundesliga gewonnen. Trainer Thomas Müller "
                "zeigte sich zufrieden mit der Leistung seiner Mannschaft."
            ),
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["relevant"] is False

    def test_search(self, client: httpx.Client):
        resp = client.post("/search", json={
            "query": "Sozialpolitik Hessen",
            "n_results": 5,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "Sozialpolitik Hessen"
        assert len(data["results"]) > 0
        assert data["total_in_store"] > 0
        for result in data["results"]:
            assert 0 <= result["score"] <= 1
            assert "id" in result
            assert "title" in result

    def test_find_duplicates_no_match(self, client: httpx.Client):
        unique = uuid.uuid4().hex
        resp = client.post("/find-duplicates", json={
            "title": f"Completely unique nonsense {unique}",
            "content": f"Random gibberish text {unique} with no real meaning.",
            "threshold": 0.99,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_duplicates"] is False
        assert len(data["duplicates"]) == 0


# ---------------------------------------------------------------------------
# Write tests: index → verify → search → delete
# ---------------------------------------------------------------------------


class TestWriteEndpoints:
    """Tests that create and clean up test data.

    Methods are ordered by dependency — pytest runs them top-to-bottom
    within the class.
    """

    TEST_ITEMS = [
        {
            "id": f"{TEST_ID_PREFIX}1",
            "title": "Integrationstest Artikel Eins",
            "content": "Dies ist ein Testartikel für die Classifier API Integration.",
            "metadata": {"source": "integration-test"},
        },
        {
            "id": f"{TEST_ID_PREFIX}2",
            "title": "Integrationstest Artikel Zwei",
            "content": "Zweiter Testartikel mit anderem Inhalt für Batch-Index.",
            "metadata": {"source": "integration-test"},
        },
        {
            "id": f"{TEST_ID_PREFIX}3",
            "title": "Integrationstest Artikel Drei",
            "content": "Dritter Testartikel für Batch-Indexierung und Suche.",
            "metadata": {"source": "integration-test"},
        },
    ]

    def test_01_index_single(self, client: httpx.Client):
        """Index a single test item."""
        item = self.TEST_ITEMS[0]
        resp = client.post("/index", json=item)
        assert resp.status_code == 200
        data = resp.json()
        assert data["indexed"] == 1
        assert data["total_in_store"] > 0

    def test_02_index_duplicate_skipped(self, client: httpx.Client):
        """Re-indexing the same item should be a no-op."""
        item = self.TEST_ITEMS[0]
        resp = client.post("/index", json=item)
        assert resp.status_code == 200
        data = resp.json()
        assert data["indexed"] == 0

    def test_03_index_batch(self, client: httpx.Client):
        """Batch index two more test items."""
        items = self.TEST_ITEMS[1:3]
        resp = client.post("/index/batch", json={"items": items})
        assert resp.status_code == 200
        data = resp.json()
        assert data["indexed"] == 2

    def test_04_ids_contain_test_items(self, client: httpx.Client):
        """All three test items should be in the index."""
        resp = client.get("/ids")
        assert resp.status_code == 200
        all_ids = set(resp.json()["ids"])
        for item in self.TEST_ITEMS:
            assert item["id"] in all_ids, f"{item['id']} not found in index"

    def test_05_find_duplicates_match(self, client: httpx.Client):
        """Searching for content similar to an indexed item should find it."""
        item = self.TEST_ITEMS[0]
        resp = client.post("/find-duplicates", json={
            "title": item["title"],
            "content": item["content"],
            "threshold": 0.5,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_duplicates"] is True
        matched_ids = [d["id"] for d in data["duplicates"]]
        assert item["id"] in matched_ids

    def test_06_similar(self, client: httpx.Client):
        """Find items similar to our indexed test item."""
        resp = client.post("/similar", json={
            "item_id": f"{TEST_ID_PREFIX}1",
            "n_results": 5,
            "exclude_same_source": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) > 0

    def test_07_delete(self, client: httpx.Client):
        """Delete all test items from both indexes."""
        test_ids = [item["id"] for item in self.TEST_ITEMS]
        resp = client.post("/delete", json={"ids": test_ids})
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_from_search"] == 3
        assert data["deleted_from_duplicate"] == 3

    def test_08_ids_no_longer_contain_test_items(self, client: httpx.Client):
        """Verify test items were actually removed."""
        resp = client.get("/ids")
        assert resp.status_code == 200
        all_ids = set(resp.json()["ids"])
        for item in self.TEST_ITEMS:
            assert item["id"] not in all_ids, f"{item['id']} still in index"


# ---------------------------------------------------------------------------
# Sync test
# ---------------------------------------------------------------------------


class TestSync:

    def test_sync_duplicate_store(self, client: httpx.Client):
        resp = client.post("/sync-duplicate-store")
        assert resp.status_code == 200
        data = resp.json()
        assert "synced" in data
        assert "skipped" in data
        assert data["total_in_duplicate_index"] > 0
