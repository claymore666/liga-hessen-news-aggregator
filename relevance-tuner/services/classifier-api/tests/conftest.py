"""Fixtures for classifier API integration tests."""

import os

import httpx
import pytest

CLASSIFIER_URL = os.environ.get("CLASSIFIER_URL", "http://localhost:8082")
TEST_ID_PREFIX = "test-integ-"


@pytest.fixture(scope="session")
def base_url():
    return CLASSIFIER_URL


@pytest.fixture(scope="session")
def client(base_url):
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_items(client):
    """Delete any test items after all tests complete (even on failure)."""
    yield
    # Find and delete any leftover test items
    try:
        resp = client.get("/ids")
        if resp.status_code == 200:
            all_ids = resp.json().get("ids", [])
            test_ids = [i for i in all_ids if i.startswith(TEST_ID_PREFIX)]
            if test_ids:
                client.post("/delete", json={"ids": test_ids})
    except Exception:
        pass  # Best-effort cleanup
