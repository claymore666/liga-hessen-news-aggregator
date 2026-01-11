"""Tests for LLM API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Item, Setting


class TestLLMStatus:
    """Tests for GET /api/llm/status endpoint."""

    @pytest.mark.asyncio
    async def test_llm_status(self, client: AsyncClient):
        """Get LLM status."""
        response = await client.get("/api/llm/status")

        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "env_enabled" in data
        assert "runtime_enabled" in data
        assert "provider" in data
        assert "model" in data
        assert "ollama_available" in data
        assert "unprocessed_count" in data

    @pytest.mark.asyncio
    async def test_llm_status_unprocessed_count(
        self, client: AsyncClient, item_in_db: Item
    ):
        """Status shows unprocessed items count."""
        response = await client.get("/api/llm/status")

        assert response.status_code == 200
        data = response.json()
        # item_in_db has no summary, so should be counted as unprocessed
        assert data["unprocessed_count"] >= 1


class TestLLMEnableDisable:
    """Tests for enable/disable LLM endpoints."""

    @pytest.mark.asyncio
    async def test_disable_llm(self, client: AsyncClient):
        """POST /api/llm/disable disables LLM."""
        response = await client.post("/api/llm/disable")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["enabled"] is False
        assert "disabled" in data["message"].lower()

        # Verify status changed
        status = await client.get("/api/llm/status")
        # Runtime enabled should be False
        assert status.json()["runtime_enabled"] is False

    @pytest.mark.asyncio
    async def test_enable_llm(self, client: AsyncClient):
        """POST /api/llm/enable enables LLM."""
        response = await client.post("/api/llm/enable")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["enabled"] is True
        assert "enabled" in data["message"].lower()

    @pytest.mark.skip(reason="Requires LLM service to be available")
    @pytest.mark.asyncio
    async def test_enable_llm_with_auto_reprocess(
        self, client: AsyncClient, item_in_db: Item
    ):
        """Enabling LLM can trigger auto-reprocessing."""
        response = await client.post(
            "/api/llm/enable",
            params={"auto_reprocess": True, "reprocess_limit": 5},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # May or may not trigger reprocessing based on LLM availability
        assert "reprocess_triggered" in data

    @pytest.mark.asyncio
    async def test_enable_llm_without_auto_reprocess(self, client: AsyncClient):
        """Can disable auto-reprocessing when enabling LLM."""
        response = await client.post(
            "/api/llm/enable", params={"auto_reprocess": False}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["reprocess_triggered"] is False


class TestLLMSettings:
    """Tests for GET /api/llm/settings endpoint."""

    @pytest.mark.asyncio
    async def test_llm_settings(self, client: AsyncClient):
        """Get LLM settings."""
        response = await client.get("/api/llm/settings")

        assert response.status_code == 200
        data = response.json()
        assert "ollama_available" in data
        assert "ollama_base_url" in data
        assert "ollama_model" in data
        assert "openrouter_configured" in data
        assert "openrouter_model" in data


class TestLLMModels:
    """Tests for /api/llm/models endpoint."""

    @pytest.mark.asyncio
    async def test_list_models(self, client: AsyncClient):
        """GET /api/llm/models lists available models."""
        response = await client.get("/api/llm/models")

        # May return 200 with models or 503 if Ollama unavailable
        assert response.status_code in (200, 503)

        if response.status_code == 200:
            data = response.json()
            assert "available" in data
            assert "models" in data
            assert "current_model" in data
            assert "base_url" in data


class TestLLMModelSelection:
    """Tests for model selection endpoints."""

    @pytest.mark.asyncio
    async def test_get_selected_model(self, client: AsyncClient):
        """GET /api/llm/model returns current model."""
        response = await client.get("/api/llm/model")

        assert response.status_code == 200
        data = response.json()
        assert "model" in data
        assert "source" in data
        assert data["source"] in ("database", "config")

    @pytest.mark.asyncio
    async def test_get_selected_model_from_database(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Model selection from database takes precedence."""
        setting = Setting(
            key="ollama_model",
            value="custom-model",
            description="Test model selection",
        )
        db_session.add(setting)
        await db_session.flush()

        response = await client.get("/api/llm/model")

        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "custom-model"
        assert data["source"] == "database"


class TestLLMPrompt:
    """Tests for POST /api/llm/prompt endpoint."""

    @pytest.mark.asyncio
    async def test_prompt_validation(self, client: AsyncClient):
        """Empty prompt is rejected."""
        response = await client.post("/api/llm/prompt", json={"prompt": ""})

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_prompt_with_options(self, client: AsyncClient):
        """Prompt accepts temperature and max_tokens."""
        # This may fail if LLM is unavailable, but schema validation should pass
        response = await client.post(
            "/api/llm/prompt",
            json={
                "prompt": "Test prompt",
                "temperature": 0.5,
                "max_tokens": 100,
            },
        )

        # Either success or LLM unavailable
        assert response.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_prompt_temperature_range(self, client: AsyncClient):
        """Temperature must be in valid range."""
        # Too high
        response = await client.post(
            "/api/llm/prompt", json={"prompt": "Test", "temperature": 3.0}
        )
        assert response.status_code == 422

        # Negative
        response = await client.post(
            "/api/llm/prompt", json={"prompt": "Test", "temperature": -0.5}
        )
        assert response.status_code == 422
