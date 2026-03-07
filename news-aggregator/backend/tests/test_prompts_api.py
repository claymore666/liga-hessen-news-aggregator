"""Tests for prompts API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import LLMPrompt


@pytest.fixture
def sample_prompt_data() -> dict:
    return {
        "model": "test-model",
        "system_prompt": "You are a test assistant.",
        "notes": "Initial test prompt",
        "activate": True,
    }


@pytest.fixture
async def prompt_in_db(db_session: AsyncSession) -> LLMPrompt:
    prompt = LLMPrompt(
        model="test-model",
        version=1,
        system_prompt="You are a test assistant v1.",
        active=True,
        notes="v1 prompt",
    )
    db_session.add(prompt)
    await db_session.flush()
    return prompt


@pytest.fixture
async def multiple_prompts_in_db(db_session: AsyncSession) -> list[LLMPrompt]:
    prompts = []
    for i in range(1, 4):
        p = LLMPrompt(
            model="test-model",
            version=i,
            system_prompt=f"You are a test assistant v{i}.",
            active=(i == 2),  # v2 is active
            notes=f"v{i} prompt",
        )
        db_session.add(p)
        prompts.append(p)

    # Also add a prompt for a different model
    other = LLMPrompt(
        model="other-model",
        version=1,
        system_prompt="Other model prompt.",
        active=True,
        notes="other model v1",
    )
    db_session.add(other)
    prompts.append(other)

    await db_session.flush()
    return prompts


class TestListModels:
    """Tests for GET /api/prompts/models."""

    @pytest.mark.asyncio
    async def test_list_models_empty(self, client: AsyncClient):
        response = await client.get("/api/prompts/models")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_models_with_data(
        self, client: AsyncClient, multiple_prompts_in_db: list[LLMPrompt]
    ):
        response = await client.get("/api/prompts/models")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        models = {m["model"] for m in data}
        assert models == {"test-model", "other-model"}

        test_model = next(m for m in data if m["model"] == "test-model")
        assert test_model["latest_version"] == 3
        assert test_model["active_version"] == 2
        assert test_model["total_versions"] == 3

    @pytest.mark.asyncio
    async def test_list_models_shows_active_version(
        self, client: AsyncClient, prompt_in_db: LLMPrompt
    ):
        response = await client.get("/api/prompts/models")
        data = response.json()
        assert len(data) == 1
        assert data[0]["active_version"] == 1


class TestGetActivePrompt:
    """Tests for GET /api/prompts/{model}."""

    @pytest.mark.asyncio
    async def test_get_active_prompt(
        self, client: AsyncClient, prompt_in_db: LLMPrompt
    ):
        response = await client.get("/api/prompts/test-model")
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "test-model"
        assert data["version"] == 1
        assert data["active"] is True
        assert data["system_prompt"] is not None  # Full text included

    @pytest.mark.asyncio
    async def test_get_active_prompt_not_found(self, client: AsyncClient):
        response = await client.get("/api/prompts/nonexistent")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_active_returns_correct_version(
        self, client: AsyncClient, multiple_prompts_in_db: list[LLMPrompt]
    ):
        response = await client.get("/api/prompts/test-model")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 2  # v2 is active


class TestGetPromptHistory:
    """Tests for GET /api/prompts/{model}/history."""

    @pytest.mark.asyncio
    async def test_history(
        self, client: AsyncClient, multiple_prompts_in_db: list[LLMPrompt]
    ):
        response = await client.get("/api/prompts/test-model/history")
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "test-model"
        assert data["active_version"] == 2
        assert len(data["versions"]) == 3
        # Ordered by version desc
        versions = [v["version"] for v in data["versions"]]
        assert versions == [3, 2, 1]
        # system_prompt not included in history listing
        assert all(v["system_prompt"] is None for v in data["versions"])

    @pytest.mark.asyncio
    async def test_history_not_found(self, client: AsyncClient):
        response = await client.get("/api/prompts/nonexistent/history")
        assert response.status_code == 404


class TestGetPromptVersion:
    """Tests for GET /api/prompts/{model}/v/{version}."""

    @pytest.mark.asyncio
    async def test_get_specific_version(
        self, client: AsyncClient, multiple_prompts_in_db: list[LLMPrompt]
    ):
        response = await client.get("/api/prompts/test-model/v/3")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 3
        assert data["active"] is False
        assert "v3" in data["system_prompt"]

    @pytest.mark.asyncio
    async def test_get_version_not_found(self, client: AsyncClient):
        response = await client.get("/api/prompts/test-model/v/999")
        assert response.status_code == 404


class TestCreatePrompt:
    """Tests for POST /api/prompts/admin."""

    @pytest.mark.asyncio
    async def test_create_first_prompt(
        self, client: AsyncClient, sample_prompt_data: dict
    ):
        response = await client.post("/api/prompts/admin", json=sample_prompt_data)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["prompt"]["version"] == 1
        assert data["prompt"]["model"] == "test-model"
        assert data["prompt"]["active"] is True

    @pytest.mark.asyncio
    async def test_create_auto_increments_version(
        self, client: AsyncClient, prompt_in_db: LLMPrompt
    ):
        response = await client.post("/api/prompts/admin", json={
            "model": "test-model",
            "system_prompt": "Updated prompt.",
            "activate": True,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["prompt"]["version"] == 2

    @pytest.mark.asyncio
    async def test_create_deactivates_previous(
        self, client: AsyncClient, prompt_in_db: LLMPrompt
    ):
        # Create v2 with activate=True
        await client.post("/api/prompts/admin", json={
            "model": "test-model",
            "system_prompt": "v2 prompt.",
            "activate": True,
        })

        # v1 should no longer be active
        response = await client.get("/api/prompts/test-model/v/1")
        assert response.json()["active"] is False

        # v2 should be active
        response = await client.get("/api/prompts/test-model")
        assert response.json()["version"] == 2

    @pytest.mark.asyncio
    async def test_create_without_activate(
        self, client: AsyncClient, prompt_in_db: LLMPrompt
    ):
        response = await client.post("/api/prompts/admin", json={
            "model": "test-model",
            "system_prompt": "Inactive draft.",
            "activate": False,
        })
        assert response.status_code == 200
        assert response.json()["prompt"]["active"] is False

        # v1 should still be active
        response = await client.get("/api/prompts/test-model")
        assert response.json()["version"] == 1


class TestActivatePrompt:
    """Tests for POST /api/prompts/admin/{model}/activate/{version}."""

    @pytest.mark.asyncio
    async def test_activate_version(
        self, client: AsyncClient, multiple_prompts_in_db: list[LLMPrompt]
    ):
        # v2 is currently active, activate v3
        response = await client.post("/api/prompts/admin/test-model/activate/3")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["prompt"]["version"] == 3
        assert data["prompt"]["active"] is True

        # v2 should be deactivated
        response = await client.get("/api/prompts/test-model/v/2")
        assert response.json()["active"] is False

        # Active prompt should now be v3
        response = await client.get("/api/prompts/test-model")
        assert response.json()["version"] == 3

    @pytest.mark.asyncio
    async def test_activate_nonexistent(self, client: AsyncClient):
        response = await client.post("/api/prompts/admin/test-model/activate/999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_activate_does_not_affect_other_models(
        self, client: AsyncClient, multiple_prompts_in_db: list[LLMPrompt]
    ):
        # Activate v3 for test-model
        await client.post("/api/prompts/admin/test-model/activate/3")

        # other-model v1 should still be active
        response = await client.get("/api/prompts/other-model")
        assert response.status_code == 200
        assert response.json()["active"] is True
