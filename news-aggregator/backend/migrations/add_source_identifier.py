"""Migration: Add source_identifier column with unique index.

This migration adds a source_identifier column to prevent duplicate sources.
Run with: python migrations/add_source_identifier.py
"""

import asyncio
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from database import engine


async def migrate():
    """Add source_identifier column and populate existing data."""
    async with engine.begin() as conn:
        # Check if column already exists
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'sources' AND column_name = 'source_identifier'
        """))

        if result.fetchone():
            print("Column 'source_identifier' already exists, skipping creation.")
        else:
            # Add the new column
            print("Adding 'source_identifier' column...")
            await conn.execute(
                text("ALTER TABLE sources ADD COLUMN source_identifier VARCHAR(500)")
            )
            print("Column added.")

        # Populate identifiers for existing sources
        print("Populating identifiers for existing sources...")

        # Get all sources
        result = await conn.execute(
            text("SELECT id, connector_type, config FROM sources")
        )
        sources = result.fetchall()

        import json

        for source_id, connector_type, config_str in sources:
            config = json.loads(config_str) if isinstance(config_str, str) else config_str

            identifier = None
            if connector_type in ("x_scraper", "twitter"):
                identifier = config.get("username", "").lower()
            elif connector_type in ("mastodon", "bluesky"):
                identifier = config.get("handle", "").lower()
            elif connector_type in ("rss", "html", "pdf"):
                identifier = config.get("url", "").lower()

            if identifier:
                await conn.execute(
                    text("UPDATE sources SET source_identifier = :identifier WHERE id = :id"),
                    {"identifier": identifier, "id": source_id},
                )

        print(f"Updated {len(sources)} sources.")

        # Check for duplicates before creating unique index
        print("Checking for duplicates...")
        result = await conn.execute(
            text("""
                SELECT connector_type, source_identifier, COUNT(*) as cnt
                FROM sources
                WHERE source_identifier IS NOT NULL
                GROUP BY connector_type, source_identifier
                HAVING COUNT(*) > 1
            """)
        )
        duplicates = result.fetchall()

        if duplicates:
            print("\n⚠️  Found duplicates that must be resolved before creating unique index:")
            for connector_type, identifier, count in duplicates:
                print(f"  - {connector_type}: '{identifier}' ({count} sources)")

                # Show the duplicate sources
                result = await conn.execute(
                    text("""
                        SELECT id, name FROM sources
                        WHERE connector_type = :type AND source_identifier = :identifier
                    """),
                    {"type": connector_type, "identifier": identifier},
                )
                for source_id, name in result.fetchall():
                    print(f"      ID {source_id}: {name}")

            print("\nPlease delete duplicates manually, then run this migration again.")
            return False

        # Create unique index
        print("Creating unique index...")
        try:
            await conn.execute(
                text("""
                    CREATE UNIQUE INDEX IF NOT EXISTS ix_sources_unique_identifier
                    ON sources (connector_type, source_identifier)
                """)
            )
            print("Unique index created.")
        except Exception as e:
            print(f"Error creating index: {e}")
            return False

        print("\n✅ Migration completed successfully!")
        return True


if __name__ == "__main__":
    success = asyncio.run(migrate())
    sys.exit(0 if success else 1)
