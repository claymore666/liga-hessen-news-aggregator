"""Tests for config import/export API endpoints."""

from datetime import datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, ConnectorType, Rule, RuleType, Setting, Source


class TestConfigExport:
    """Tests for GET /api/admin/config/export endpoint."""

    @pytest.mark.asyncio
    async def test_export_empty(self, client: AsyncClient):
        """Export with empty database."""
        response = await client.get("/api/admin/config/export")

        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert "instance_identifier" in data
        assert "exported_at" in data
        assert data["sources"] == []
        assert data["rules"] == []
        assert data["settings"] == []

    @pytest.mark.asyncio
    async def test_export_with_sources(
        self, client: AsyncClient, source_in_db: Source, channel_in_db: Channel
    ):
        """Export includes sources and channels."""
        response = await client.get("/api/admin/config/export")

        assert response.status_code == 200
        data = response.json()
        assert len(data["sources"]) >= 1

        source = data["sources"][0]
        assert source["name"] == source_in_db.name
        assert len(source["channels"]) >= 1

    @pytest.mark.asyncio
    async def test_export_with_rules(
        self, client: AsyncClient, rule_in_db: Rule
    ):
        """Export includes rules."""
        response = await client.get("/api/admin/config/export")

        assert response.status_code == 200
        data = response.json()
        assert len(data["rules"]) >= 1

        rule = data["rules"][0]
        assert rule["name"] == rule_in_db.name
        assert rule["pattern"] == rule_in_db.pattern

    @pytest.mark.asyncio
    async def test_export_with_settings(
        self, client: AsyncClient, setting_in_db: Setting
    ):
        """Export includes settings."""
        response = await client.get("/api/admin/config/export")

        assert response.status_code == 200
        data = response.json()
        assert len(data["settings"]) >= 1

    @pytest.mark.asyncio
    async def test_export_redacts_sensitive_by_default(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Sensitive config values are redacted by default."""
        source = Source(name="Sensitive Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.X_SCRAPER,
            config={"handle": "@test", "cookies": "secret_cookie_value"},
        )
        db_session.add(channel)
        await db_session.flush()

        response = await client.get("/api/admin/config/export")

        assert response.status_code == 200
        data = response.json()
        source_data = next(s for s in data["sources"] if s["name"] == "Sensitive Test")
        channel_data = source_data["channels"][0]
        # Cookies should be redacted
        assert channel_data["config"].get("cookies") == "<REDACTED>"

    @pytest.mark.asyncio
    async def test_export_includes_sensitive_when_requested(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Sensitive config values included when requested."""
        source = Source(name="Full Export Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.X_SCRAPER,
            config={"handle": "@test", "cookies": "actual_secret"},
        )
        db_session.add(channel)
        await db_session.flush()

        response = await client.get(
            "/api/admin/config/export", params={"include_sensitive": True}
        )

        assert response.status_code == 200
        data = response.json()
        source_data = next(s for s in data["sources"] if s["name"] == "Full Export Test")
        channel_data = source_data["channels"][0]
        # Cookies should NOT be redacted
        assert channel_data["config"].get("cookies") == "actual_secret"

    @pytest.mark.asyncio
    async def test_export_custom_instance_identifier(self, client: AsyncClient):
        """Can specify custom instance identifier."""
        response = await client.get(
            "/api/admin/config/export",
            params={"instance_identifier": "custom-instance"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["instance_identifier"] == "custom-instance"


class TestConfigValidate:
    """Tests for POST /api/admin/config/validate endpoint."""

    @pytest.mark.asyncio
    async def test_validate_valid_config(
        self, client: AsyncClient, sample_config_export: dict[str, Any]
    ):
        """Validate valid configuration."""
        response = await client.post(
            "/api/admin/config/validate", json=sample_config_export
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_validate_duplicate_source_names(self, client: AsyncClient):
        """Duplicate source names are detected."""
        config = {
            "version": "1.0",
            "instance_identifier": "test",
            "exported_at": datetime.utcnow().isoformat(),
            "sources": [
                {"name": "Duplicate Name", "channels": []},
                {"name": "Duplicate Name", "channels": []},
            ],
            "rules": [],
            "settings": [],
        }

        response = await client.post("/api/admin/config/validate", json=config)

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert len(data["errors"]) >= 1
        assert any("duplicate" in e["message"].lower() for e in data["errors"])

    @pytest.mark.asyncio
    async def test_validate_duplicate_rule_names(self, client: AsyncClient):
        """Duplicate rule names are detected."""
        config = {
            "version": "1.0",
            "instance_identifier": "test",
            "exported_at": datetime.utcnow().isoformat(),
            "sources": [],
            "rules": [
                {"name": "Same Rule", "rule_type": "keyword", "pattern": "a", "priority_boost": 0, "enabled": True, "order": 0},
                {"name": "Same Rule", "rule_type": "keyword", "pattern": "b", "priority_boost": 0, "enabled": True, "order": 1},
            ],
            "settings": [],
        }

        response = await client.post("/api/admin/config/validate", json=config)

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert any("duplicate" in e["message"].lower() for e in data["errors"])

    @pytest.mark.asyncio
    async def test_validate_invalid_regex(self, client: AsyncClient):
        """Invalid regex patterns are detected."""
        config = {
            "version": "1.0",
            "instance_identifier": "test",
            "exported_at": datetime.utcnow().isoformat(),
            "sources": [],
            "rules": [
                {"name": "Bad Regex", "rule_type": "regex", "pattern": "[invalid(", "priority_boost": 0, "enabled": True, "order": 0},
            ],
            "settings": [],
        }

        response = await client.post("/api/admin/config/validate", json=config)

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert any("regex" in e["message"].lower() for e in data["errors"])

    @pytest.mark.asyncio
    async def test_validate_redacted_values_not_replaced(self, client: AsyncClient):
        """Redacted values that weren't replaced are detected."""
        config = {
            "version": "1.0",
            "instance_identifier": "test",
            "exported_at": datetime.utcnow().isoformat(),
            "sources": [
                {
                    "name": "Source with redacted",
                    "channels": [
                        {
                            "connector_type": "x_scraper",
                            "config": {"handle": "@test", "cookies": "<REDACTED>"},
                            "enabled": True,
                            "fetch_interval_minutes": 30,
                        }
                    ],
                }
            ],
            "rules": [],
            "settings": [],
        }

        response = await client.post("/api/admin/config/validate", json=config)

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert any("redacted" in e["message"].lower() for e in data["errors"])

    @pytest.mark.asyncio
    async def test_validate_version_warning(self, client: AsyncClient):
        """Version mismatch produces warning."""
        config = {
            "version": "0.1",  # Old version
            "instance_identifier": "test",
            "exported_at": datetime.utcnow().isoformat(),
            "sources": [],
            "rules": [],
            "settings": [],
        }

        response = await client.post("/api/admin/config/validate", json=config)

        assert response.status_code == 200
        data = response.json()
        # Should still be valid but with warning
        assert len(data["warnings"]) >= 1
        assert any("version" in w["message"].lower() for w in data["warnings"])


class TestConfigImport:
    """Tests for POST /api/admin/config/import endpoint."""

    @pytest.mark.asyncio
    async def test_import_merge_mode(
        self, client: AsyncClient, sample_config_export: dict[str, Any]
    ):
        """Import in merge mode adds new items."""
        response = await client.post(
            "/api/admin/config/import",
            params={"mode": "merge"},
            json=sample_config_export,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["imported"]["sources"] >= 1
        assert data["imported"]["rules"] >= 1

    @pytest.mark.asyncio
    async def test_import_merge_skips_existing(
        self, client: AsyncClient, source_in_db: Source
    ):
        """Merge mode skips existing items."""
        config = {
            "version": "1.0",
            "instance_identifier": "test",
            "exported_at": datetime.utcnow().isoformat(),
            "sources": [
                {"name": source_in_db.name, "channels": []},  # Same name as existing
            ],
            "rules": [],
            "settings": [],
        }

        response = await client.post(
            "/api/admin/config/import",
            params={"mode": "merge"},
            json=config,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["skipped"]["sources"] >= 1

    @pytest.mark.asyncio
    async def test_import_replace_mode(
        self, client: AsyncClient, sample_config_export: dict[str, Any], source_in_db: Source
    ):
        """Import in replace mode clears existing config."""
        # First, verify we have existing data
        export_before = await client.get("/api/admin/config/export")
        assert len(export_before.json()["sources"]) >= 1

        # Import with replace mode
        response = await client.post(
            "/api/admin/config/import",
            params={"mode": "replace"},
            json=sample_config_export,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_import_invalid_config_rejected(self, client: AsyncClient):
        """Invalid configuration is rejected."""
        config = {
            "version": "1.0",
            "instance_identifier": "test",
            "exported_at": datetime.utcnow().isoformat(),
            "sources": [
                {"name": "Duplicate", "channels": []},
                {"name": "Duplicate", "channels": []},  # Duplicate
            ],
            "rules": [],
            "settings": [],
        }

        response = await client.post(
            "/api/admin/config/import",
            params={"mode": "merge"},
            json=config,
        )

        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_import_creates_channels(
        self, client: AsyncClient
    ):
        """Import creates channels for sources."""
        config = {
            "version": "1.0",
            "instance_identifier": "test",
            "exported_at": datetime.utcnow().isoformat(),
            "sources": [
                {
                    "name": "Import Channel Test",
                    "channels": [
                        {
                            "name": "Test RSS",
                            "connector_type": "rss",
                            "config": {"url": "https://import-test.com/feed"},
                            "enabled": True,
                            "fetch_interval_minutes": 60,
                        }
                    ],
                }
            ],
            "rules": [],
            "settings": [],
        }

        response = await client.post(
            "/api/admin/config/import",
            params={"mode": "merge"},
            json=config,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["imported"]["sources"] == 1
        assert data["imported"]["channels"] == 1

    @pytest.mark.asyncio
    async def test_import_default_mode_is_merge(self, client: AsyncClient):
        """Default import mode is merge."""
        config = {
            "version": "1.0",
            "instance_identifier": "test",
            "exported_at": datetime.utcnow().isoformat(),
            "sources": [{"name": "Default Mode Test", "channels": []}],
            "rules": [],
            "settings": [],
        }

        # Don't specify mode
        response = await client.post("/api/admin/config/import", json=config)

        assert response.status_code == 200
        data = response.json()
        # Should have imported (merge behavior)
        assert data["imported"]["sources"] == 1


class TestExportImportRoundtrip:
    """Tests for export/import roundtrip."""

    @pytest.mark.asyncio
    async def test_export_import_roundtrip(
        self,
        client: AsyncClient,
        source_in_db: Source,
        channel_in_db: Channel,
        rule_in_db: Rule,
    ):
        """Export and re-import produces same config."""
        # Export current config
        export_response = await client.get(
            "/api/admin/config/export", params={"include_sensitive": True}
        )
        assert export_response.status_code == 200
        exported = export_response.json()

        # Validate exported config
        validate_response = await client.post(
            "/api/admin/config/validate", json=exported
        )
        assert validate_response.status_code == 200
        assert validate_response.json()["valid"] is True

        # Import should succeed (merge mode, items already exist)
        import_response = await client.post(
            "/api/admin/config/import",
            params={"mode": "merge"},
            json=exported,
        )
        assert import_response.status_code == 200
