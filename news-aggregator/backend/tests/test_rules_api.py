"""Tests for rules API endpoints."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Rule, RuleType


class TestListRules:
    """Tests for GET /api/rules endpoint."""

    @pytest.mark.asyncio
    async def test_list_rules_empty(self, client: AsyncClient):
        """Returns empty list when no rules exist."""
        response = await client.get("/api/rules")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_rules_with_data(
        self, client: AsyncClient, rule_in_db: Rule
    ):
        """Returns rules when data exists."""
        response = await client.get("/api/rules")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(r["id"] == rule_in_db.id for r in data)

    @pytest.mark.asyncio
    async def test_list_rules_ordered(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Rules are returned ordered by 'order' field."""
        rule1 = Rule(name="Rule Z", rule_type=RuleType.KEYWORD, pattern="z", order=2)
        rule2 = Rule(name="Rule A", rule_type=RuleType.KEYWORD, pattern="a", order=1)
        rule3 = Rule(name="Rule M", rule_type=RuleType.KEYWORD, pattern="m", order=0)
        db_session.add_all([rule1, rule2, rule3])
        await db_session.flush()

        response = await client.get("/api/rules")

        assert response.status_code == 200
        data = response.json()
        orders = [r["order"] for r in data]
        assert orders == sorted(orders)


class TestCreateRule:
    """Tests for POST /api/rules endpoint."""

    @pytest.mark.asyncio
    async def test_create_rule_keyword(
        self, client: AsyncClient, sample_rule_data: dict[str, Any]
    ):
        """Create keyword rule."""
        response = await client.post("/api/rules", json=sample_rule_data)

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == sample_rule_data["name"]
        assert data["rule_type"] == "keyword"
        assert data["pattern"] == sample_rule_data["pattern"]
        assert data["priority_boost"] == sample_rule_data["priority_boost"]
        assert "id" in data

    @pytest.mark.asyncio
    async def test_create_rule_regex(
        self, client: AsyncClient, sample_regex_rule_data: dict[str, Any]
    ):
        """Create regex rule."""
        response = await client.post("/api/rules", json=sample_regex_rule_data)

        assert response.status_code == 201
        data = response.json()
        assert data["rule_type"] == "regex"
        assert data["target_priority"] == "high"

    @pytest.mark.asyncio
    async def test_create_rule_minimal(self, client: AsyncClient):
        """Create rule with minimal data."""
        response = await client.post(
            "/api/rules",
            json={
                "name": "Minimal Rule",
                "rule_type": "keyword",
                "pattern": "test",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Minimal Rule"
        assert data["priority_boost"] == 0  # Default
        assert data["enabled"] is True  # Default

    @pytest.mark.asyncio
    async def test_create_rule_empty_name(self, client: AsyncClient):
        """Empty name is rejected."""
        response = await client.post(
            "/api/rules",
            json={"name": "", "rule_type": "keyword", "pattern": "test"},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_rule_empty_pattern(self, client: AsyncClient):
        """Empty pattern is rejected."""
        response = await client.post(
            "/api/rules",
            json={"name": "Test", "rule_type": "keyword", "pattern": ""},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_rule_invalid_priority_boost(self, client: AsyncClient):
        """Priority boost out of range is rejected."""
        # Too high
        response = await client.post(
            "/api/rules",
            json={
                "name": "Test",
                "rule_type": "keyword",
                "pattern": "test",
                "priority_boost": 150,
            },
        )
        assert response.status_code == 422

        # Too low
        response = await client.post(
            "/api/rules",
            json={
                "name": "Test",
                "rule_type": "keyword",
                "pattern": "test",
                "priority_boost": -150,
            },
        )
        assert response.status_code == 422


class TestGetRule:
    """Tests for GET /api/rules/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_rule(self, client: AsyncClient, rule_in_db: Rule):
        """Get rule by ID."""
        response = await client.get(f"/api/rules/{rule_in_db.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == rule_in_db.id
        assert data["name"] == rule_in_db.name

    @pytest.mark.asyncio
    async def test_get_rule_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent rule."""
        response = await client.get("/api/rules/99999")

        assert response.status_code == 404


class TestUpdateRule:
    """Tests for PATCH /api/rules/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_rule_name(self, client: AsyncClient, rule_in_db: Rule):
        """Update rule name."""
        response = await client.patch(
            f"/api/rules/{rule_in_db.id}", json={"name": "Updated Rule Name"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Rule Name"

    @pytest.mark.asyncio
    async def test_update_rule_pattern(self, client: AsyncClient, rule_in_db: Rule):
        """Update rule pattern."""
        response = await client.patch(
            f"/api/rules/{rule_in_db.id}", json={"pattern": "new, pattern, words"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["pattern"] == "new, pattern, words"

    @pytest.mark.asyncio
    async def test_update_rule_priority_boost(
        self, client: AsyncClient, rule_in_db: Rule
    ):
        """Update rule priority boost."""
        response = await client.patch(
            f"/api/rules/{rule_in_db.id}", json={"priority_boost": 50}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["priority_boost"] == 50

    @pytest.mark.asyncio
    async def test_update_rule_enabled(self, client: AsyncClient, rule_in_db: Rule):
        """Update rule enabled status."""
        response = await client.patch(
            f"/api/rules/{rule_in_db.id}", json={"enabled": False}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_rule_target_priority(
        self, client: AsyncClient, rule_in_db: Rule
    ):
        """Update rule target priority."""
        response = await client.patch(
            f"/api/rules/{rule_in_db.id}", json={"target_priority": "high"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["target_priority"] == "high"

    @pytest.mark.asyncio
    async def test_update_rule_order(self, client: AsyncClient, rule_in_db: Rule):
        """Update rule order."""
        response = await client.patch(
            f"/api/rules/{rule_in_db.id}", json={"order": 5}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["order"] == 5

    @pytest.mark.asyncio
    async def test_update_rule_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent rule."""
        response = await client.patch(
            "/api/rules/99999", json={"name": "New Name"}
        )

        assert response.status_code == 404


class TestDeleteRule:
    """Tests for DELETE /api/rules/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_delete_rule(self, client: AsyncClient, rule_in_db: Rule):
        """Delete rule."""
        response = await client.delete(f"/api/rules/{rule_in_db.id}")

        assert response.status_code == 204

        # Verify deletion
        get_response = await client.get(f"/api/rules/{rule_in_db.id}")
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_rule_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent rule."""
        response = await client.delete("/api/rules/99999")

        assert response.status_code == 404


class TestReorderRules:
    """Tests for POST /api/rules/reorder endpoint."""

    @pytest.mark.asyncio
    async def test_reorder_rules(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Reorder rules."""
        rule1 = Rule(name="Rule 1", rule_type=RuleType.KEYWORD, pattern="a", order=0)
        rule2 = Rule(name="Rule 2", rule_type=RuleType.KEYWORD, pattern="b", order=1)
        rule3 = Rule(name="Rule 3", rule_type=RuleType.KEYWORD, pattern="c", order=2)
        db_session.add_all([rule1, rule2, rule3])
        await db_session.flush()

        # Reorder: rule3 first, rule1 second, rule2 third
        response = await client.post(
            "/api/rules/reorder",
            json=[
                {"id": rule3.id, "order": 0},
                {"id": rule1.id, "order": 1},
                {"id": rule2.id, "order": 2},
            ],
        )

        assert response.status_code == 200

        # Verify new order
        list_response = await client.get("/api/rules")
        rules = list_response.json()
        # Filter to just our test rules by name
        test_rules = [r for r in rules if r["name"].startswith("Rule ")]
        assert test_rules[0]["name"] == "Rule 3"
        assert test_rules[1]["name"] == "Rule 1"
        assert test_rules[2]["name"] == "Rule 2"


class TestTestRule:
    """Tests for POST /api/rules/{id}/test endpoint."""

    @pytest.mark.asyncio
    async def test_test_rule(
        self, client: AsyncClient, rule_in_db: Rule, item_in_db
    ):
        """Test rule against items."""
        response = await client.post(
            f"/api/rules/{rule_in_db.id}/test",
            params={"content": "This is a test example text"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "matches" in data or "matched" in data or "result" in data

    @pytest.mark.asyncio
    async def test_test_rule_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent rule."""
        response = await client.post(
            "/api/rules/99999/test", params={"content": "test"}
        )

        assert response.status_code == 404
