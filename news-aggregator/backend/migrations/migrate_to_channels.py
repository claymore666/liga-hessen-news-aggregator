"""Migration: Transform sources to multi-channel organization model.

This migration:
1. Creates a new 'channels' table
2. Groups existing sources into organizations
3. Migrates source configs to channels
4. Updates items to reference channels

Run with: python migrations/migrate_to_channels.py
Dry run:  python migrations/migrate_to_channels.py --dry-run
"""

import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import inspect, text
from database import engine

# Platform suffixes to strip from names (order matters - check longer patterns first)
PLATFORM_SUFFIXES = [
    (r"\s*\(X\.com\)$", "x_scraper"),
    (r"\s*\(Mastodon\)$", "mastodon"),
    (r"\s*\(Bluesky\)$", "bluesky"),
    (r"\s+Instagram$", "instagram_scraper"),
    (r"\s+Telegram$", "telegram"),
]

# Manual groupings for ambiguous cases
# Format: "original source name" -> ("organization name", "channel name or None")
MANUAL_MAPPINGS = {
    # FAZ family - multiple RSS feeds
    "FAZ Aktuell": ("FAZ (Frankfurter Allgemeine)", "Aktuell"),
    "FAZ Gesellschaft": ("FAZ (Frankfurter Allgemeine)", "Gesellschaft"),
    "FAZ Rhein-Main": ("FAZ (Frankfurter Allgemeine)", "Rhein-Main"),
    "FAZ (X.com)": ("FAZ (Frankfurter Allgemeine)", None),
    # Frankfurter Rundschau family
    "Frankfurter Rundschau": ("Frankfurter Rundschau", None),
    "Frankfurter Rundschau (X.com)": ("Frankfurter Rundschau", None),
    "FR Politik": ("Frankfurter Rundschau", "Politik"),
    # Hessenschau - case normalization
    "Hessenschau": ("Hessenschau", None),
    "hessenschau (X.com)": ("Hessenschau", None),
    # HLS (Suchtfragen)
    "HLS (Suchtfragen)": ("HLS (Suchtfragen)", None),
    "HLS Suchtfragen (X.com)": ("HLS (Suchtfragen)", None),
    # PRO ASYL - case normalization
    "Pro Asyl": ("PRO ASYL", None),
    "PRO ASYL (X.com)": ("PRO ASYL", None),
    "PRO ASYL (Bluesky)": ("PRO ASYL", None),
    # Boris Rhein - normalize "MP" suffix
    "Boris Rhein (X.com)": ("Boris Rhein", None),
    "Boris Rhein MP (Mastodon)": ("Boris Rhein", None),
    "Boris Rhein MP Instagram": ("Boris Rhein", None),
    # BMAS
    "BMAS (Arbeitsministerium)": ("BMAS (Arbeitsministerium)", None),
    "BMAS (Arbeitsministerium) (X.com)": ("BMAS (Arbeitsministerium)", None),
    # Astrid Wallmann
    "Astrid Wallmann (Landtagspräsidentin) (X.com)": ("Astrid Wallmann", None),
    "Astrid Wallmann Instagram": ("Astrid Wallmann", None),
    # Ines Claus
    "Ines Claus (CDU Fraktionsvorsitzende) (X.com)": ("Ines Claus (CDU)", None),
    "Ines Claus CDU Instagram": ("Ines Claus (CDU)", None),
    # Tobias Eckert
    "Tobias Eckert (SPD Fraktionsvorsitzender) (X.com)": ("Tobias Eckert (SPD)", None),
    "Tobias Eckert SPD Instagram": ("Tobias Eckert (SPD)", None),
    # Robert Lambrou
    "Robert Lambrou (AfD Fraktionsvorsitzender) (X.com)": ("Robert Lambrou (AfD)", None),
    "Robert Lambrou AfD Instagram": ("Robert Lambrou (AfD)", None),
    # Mathias Wagner
    "Mathias Wagner (Grüne) (X.com)": ("Mathias Wagner (Grüne)", None),
    "Mathias Wagner Gruene Instagram": ("Mathias Wagner (Grüne)", None),
    # Bijan Kaffenberger
    "Bijan Kaffenberger SPD Instagram": ("Bijan Kaffenberger (SPD)", None),
    # Hessischer Landtag
    "Hessischer Landtag (Mastodon)": ("Hessischer Landtag", None),
    "Hessischer Landtag (X.com)": ("Hessischer Landtag", None),
    "Hessischer Landtag Instagram": ("Hessischer Landtag", None),
    # Landesregierung Hessen
    "Landesregierung Hessen (Mastodon)": ("Landesregierung Hessen", None),
    "Landesregierung Hessen (X.com)": ("Landesregierung Hessen", None),
    # SPD - keep Fraktion separate from party
    "SPD Hessen (Mastodon)": ("SPD Hessen", None),
    "SPD Hessen (X.com)": ("SPD Hessen", None),
    "SPD Fraktion Hessen": ("SPD Fraktion Hessen", None),
    "SPD Landtagsfraktion Hessen (X.com)": ("SPD Fraktion Hessen", None),
    # CDU
    "CDU Hessen (X.com)": ("CDU Hessen", None),
    "CDU Fraktion Hessen (X.com)": ("CDU Fraktion Hessen", None),
    # AfD
    "AfD Hessen (X.com)": ("AfD Hessen", None),
    "AfD Fraktion Hessen (X.com)": ("AfD Fraktion Hessen", None),
    # FDP
    "FDP Hessen (X.com)": ("FDP Hessen", None),
    "FDP Fraktion Hessen": ("FDP Fraktion Hessen", None),
    # Grüne
    "Grüne Hessen (X.com)": ("Grüne Hessen", None),
    "Grüne Fraktion Hessen": ("Grüne Fraktion Hessen", None),
}


def parse_source_name(name: str, connector_type: str) -> tuple[str, str | None]:
    """
    Parse a source name to extract organization name and optional channel name.

    Returns:
        (organization_name, channel_name_or_none)
    """
    # Check manual mappings first
    if name in MANUAL_MAPPINGS:
        return MANUAL_MAPPINGS[name]

    # Strip platform suffixes
    org_name = name
    for pattern, _ in PLATFORM_SUFFIXES:
        org_name = re.sub(pattern, "", org_name, flags=re.IGNORECASE)

    return (org_name.strip(), None)


def get_channel_display_name(connector_type: str, channel_name: str | None) -> str | None:
    """Get display name for a channel based on connector type."""
    if channel_name:
        return channel_name

    # Return None - we'll use connector_type display in the UI
    return None


async def migrate(dry_run: bool = False):
    """Run the migration to transform sources to multi-channel model."""

    async with engine.begin() as conn:
        # ============================================================
        # Step 1: Check if migration already ran
        # ============================================================
        def check_table_exists(sync_conn):
            inspector = inspect(sync_conn)
            return "channels" in inspector.get_table_names()

        exists = await conn.run_sync(check_table_exists)
        if exists:
            print("Table 'channels' already exists. Migration may have already run.")
            print("If you want to re-run, drop the channels table first.")
            return False

        # ============================================================
        # Step 2: Get all current sources
        # ============================================================
        print("Reading existing sources...")
        result = await conn.execute(text("""
            SELECT id, name, connector_type, config, source_identifier,
                   enabled, is_stakeholder, fetch_interval_minutes,
                   last_fetch_at, last_error, created_at, updated_at
            FROM sources
        """))
        old_sources = result.fetchall()
        print(f"Found {len(old_sources)} sources")

        # ============================================================
        # Step 3: Group sources by organization
        # ============================================================
        print("\nGrouping sources into organizations...")
        org_groups = defaultdict(list)

        for src in old_sources:
            src_id, name, connector_type, config_str, source_identifier, \
                enabled, is_stakeholder, fetch_interval, \
                last_fetch_at, last_error, created_at, updated_at = src

            config = json.loads(config_str) if isinstance(config_str, str) else config_str

            org_name, channel_name = parse_source_name(name, connector_type)

            org_groups[org_name].append({
                "old_id": src_id,
                "old_name": name,
                "channel_name": channel_name,
                "connector_type": connector_type,
                "config": config,
                "source_identifier": source_identifier,
                "enabled": enabled,
                "is_stakeholder": is_stakeholder,
                "fetch_interval_minutes": fetch_interval,
                "last_fetch_at": last_fetch_at,
                "last_error": last_error,
                "created_at": created_at,
                "updated_at": updated_at,
            })

        print(f"Grouped into {len(org_groups)} organizations")

        # Show groupings
        print("\n--- Organization Groupings ---")
        for org_name, channels in sorted(org_groups.items()):
            if len(channels) > 1:
                print(f"\n{org_name}:")
                for ch in channels:
                    ch_display = ch['channel_name'] or ch['connector_type']
                    print(f"  - {ch['old_name']} -> [{ch_display}]")

        if dry_run:
            print("\n[DRY RUN] Would create the following structure:")
            for org_name, channels in sorted(org_groups.items()):
                print(f"\nOrg: {org_name}")
                for ch in channels:
                    print(f"  Channel: {ch['connector_type']} ({ch['channel_name'] or 'default'})")
            print("\n[DRY RUN] No changes made.")
            return True

        # ============================================================
        # Step 4: Create backup tables
        # ============================================================
        print("\nCreating backup tables...")
        await conn.execute(text("CREATE TABLE sources_backup AS SELECT * FROM sources"))
        await conn.execute(text("CREATE TABLE items_backup AS SELECT * FROM items"))
        print("Backup tables created: sources_backup, items_backup")

        # ============================================================
        # Step 5: Create channels table
        # ============================================================
        print("\nCreating channels table...")
        await conn.execute(text("""
            CREATE TABLE channels (
                id SERIAL PRIMARY KEY,
                source_id INTEGER NOT NULL,
                name VARCHAR(255),
                connector_type VARCHAR(50) NOT NULL,
                config JSON NOT NULL,
                source_identifier VARCHAR(500),
                enabled BOOLEAN DEFAULT TRUE,
                fetch_interval_minutes INTEGER DEFAULT 30,
                last_fetch_at TIMESTAMP,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
            )
        """))

        # Create indexes
        await conn.execute(text("CREATE INDEX ix_channels_source_id ON channels(source_id)"))
        await conn.execute(text("CREATE INDEX ix_channels_connector_type ON channels(connector_type)"))
        await conn.execute(text("""
            CREATE UNIQUE INDEX ix_channels_unique_identifier
            ON channels(source_id, connector_type, source_identifier)
        """))
        print("Channels table and indexes created")

        # ============================================================
        # Step 6: Add channel_id to items (nullable initially)
        # ============================================================
        print("\nAdding channel_id column to items...")
        await conn.execute(text("ALTER TABLE items ADD COLUMN channel_id INTEGER"))
        await conn.execute(text("CREATE INDEX ix_items_channel_id ON items(channel_id)"))
        print("channel_id column added")

        # ============================================================
        # Step 7: Create new sources (organizations) and channels
        # ============================================================
        print("\nCreating organizations and channels...")

        # Map old_source_id -> new_channel_id for item migration
        source_to_channel = {}

        for org_name, channels in org_groups.items():
            # Determine organization-level properties
            is_stakeholder = any(ch["is_stakeholder"] for ch in channels)
            enabled = any(ch["enabled"] for ch in channels)
            earliest_created = min(ch["created_at"] for ch in channels if ch["created_at"])

            # Insert new organization
            result = await conn.execute(
                text("""
                    INSERT INTO sources (name, connector_type, config, source_identifier,
                                        enabled, is_stakeholder, fetch_interval_minutes,
                                        created_at, updated_at)
                    VALUES (:name, 'organization', '{}', NULL, :enabled, :is_stakeholder,
                            60, :created_at, NOW())
                    RETURNING id
                """),
                {
                    "name": org_name,
                    "enabled": enabled,
                    "is_stakeholder": is_stakeholder,
                    "created_at": earliest_created,
                }
            )
            new_source_id = result.scalar()

            # Create channels for each old source in this org
            for ch in channels:
                result = await conn.execute(
                    text("""
                        INSERT INTO channels (source_id, name, connector_type, config,
                                            source_identifier, enabled, fetch_interval_minutes,
                                            last_fetch_at, last_error, created_at, updated_at)
                        VALUES (:source_id, :name, :connector_type, :config,
                                :source_identifier, :enabled, :fetch_interval_minutes,
                                :last_fetch_at, :last_error, :created_at, :updated_at)
                        RETURNING id
                    """),
                    {
                        "source_id": new_source_id,
                        "name": ch["channel_name"],
                        "connector_type": ch["connector_type"],
                        "config": json.dumps(ch["config"]),
                        "source_identifier": ch["source_identifier"],
                        "enabled": ch["enabled"],
                        "fetch_interval_minutes": ch["fetch_interval_minutes"],
                        "last_fetch_at": ch["last_fetch_at"],
                        "last_error": ch["last_error"],
                        "created_at": ch["created_at"],
                        "updated_at": ch["updated_at"],
                    }
                )
                channel_id = result.scalar()
                source_to_channel[ch["old_id"]] = channel_id

        print(f"Created {len(org_groups)} organizations with {len(source_to_channel)} channels")

        # ============================================================
        # Step 8: Update items to reference channels
        # ============================================================
        print("\nMigrating items to reference channels...")
        items_updated = 0
        for old_source_id, channel_id in source_to_channel.items():
            result = await conn.execute(
                text("UPDATE items SET channel_id = :channel_id WHERE source_id = :source_id"),
                {"channel_id": channel_id, "source_id": old_source_id}
            )
            items_updated += result.rowcount
        print(f"Updated {items_updated} items")

        # ============================================================
        # Step 9: Delete old sources (they're now in the backup)
        # ============================================================
        print("\nRemoving old source entries...")
        # Get IDs of newly created sources (they have connector_type='organization')
        result = await conn.execute(text(
            "SELECT id FROM sources WHERE connector_type = 'organization'"
        ))
        new_source_ids = [row[0] for row in result.fetchall()]

        # Delete sources that are NOT the new organizations
        await conn.execute(
            text("DELETE FROM sources WHERE connector_type != 'organization'")
        )
        print("Old sources removed")

        # ============================================================
        # Step 10: Update sources table schema
        # ============================================================
        print("\nUpdating sources table schema...")

        # Drop columns no longer needed for organization-only model
        await conn.execute(text("ALTER TABLE sources DROP COLUMN IF EXISTS connector_type"))
        await conn.execute(text("ALTER TABLE sources DROP COLUMN IF EXISTS config"))
        await conn.execute(text("ALTER TABLE sources DROP COLUMN IF EXISTS source_identifier"))
        await conn.execute(text("ALTER TABLE sources DROP COLUMN IF EXISTS fetch_interval_minutes"))
        await conn.execute(text("ALTER TABLE sources DROP COLUMN IF EXISTS last_fetch_at"))
        await conn.execute(text("ALTER TABLE sources DROP COLUMN IF EXISTS last_error"))

        # Add description column if not exists
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'sources' AND column_name = 'description'
        """))
        if not result.fetchone():
            await conn.execute(text("ALTER TABLE sources ADD COLUMN description TEXT"))

        print("Sources table schema updated")

        # ============================================================
        # Verification
        # ============================================================
        print("\n--- Migration Verification ---")

        result = await conn.execute(text("SELECT COUNT(*) FROM sources"))
        source_count = result.scalar()

        result = await conn.execute(text("SELECT COUNT(*) FROM channels"))
        channel_count = result.scalar()

        result = await conn.execute(text("SELECT COUNT(*) FROM items WHERE channel_id IS NOT NULL"))
        migrated_items = result.scalar()

        result = await conn.execute(text("SELECT COUNT(*) FROM items WHERE channel_id IS NULL"))
        unmigrated_items = result.scalar()

        print(f"Organizations (sources): {source_count}")
        print(f"Channels: {channel_count}")
        print(f"Items with channel_id: {migrated_items}")
        print(f"Items without channel_id: {unmigrated_items}")

        if unmigrated_items > 0:
            print(f"\n WARNING: {unmigrated_items} items were not migrated!")
            return False

        print("\n Migration completed successfully!")
        print("Backup tables retained: sources_backup, items_backup")
        print("Run 'DROP TABLE sources_backup; DROP TABLE items_backup;' after verifying.")
        return True


async def rollback():
    """Rollback the migration using backup tables."""
    async with engine.begin() as conn:
        print("Rolling back migration...")

        # Check if backup exists
        def check_backup_exists(sync_conn):
            inspector = inspect(sync_conn)
            return "sources_backup" in inspector.get_table_names()

        has_backup = await conn.run_sync(check_backup_exists)
        if not has_backup:
            print("No backup table found. Cannot rollback.")
            return False

        # Drop new tables
        await conn.execute(text("DROP TABLE IF EXISTS channels"))
        await conn.execute(text("DROP TABLE IF EXISTS sources"))

        # Restore from backup
        await conn.execute(text("ALTER TABLE sources_backup RENAME TO sources"))
        await conn.execute(text("ALTER TABLE items_backup RENAME TO items"))

        # Recreate indexes on sources
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sources_connector_type ON sources(connector_type)"))
        await conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_sources_unique_identifier
            ON sources(connector_type, source_identifier)
        """))

        print("Rollback completed. Original tables restored.")
        return True


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        success = asyncio.run(migrate(dry_run=True))
    elif "--rollback" in sys.argv:
        success = asyncio.run(rollback())
    else:
        success = asyncio.run(migrate())

    sys.exit(0 if success else 1)
