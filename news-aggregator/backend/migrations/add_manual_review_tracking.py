"""Add manual review tracking columns to items table.

This migration adds fields for tracking when a user manually reviews and changes
an item's priority or AK assignment. This creates verified training data for
classification improvement.

New columns:
- is_archived: Boolean flag for archived items
- assigned_ak: Direct column for AK assignment (was in metadata)
- is_manually_reviewed: Boolean flag when user changes priority/AK
- reviewed_at: Timestamp of manual review

Run with: python migrations/add_manual_review_tracking.py
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from database import engine


async def migrate():
    """Add manual review tracking columns to items table."""
    async with engine.begin() as conn:
        # Check existing columns
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'items'
        """))
        columns = [row[0] for row in result.fetchall()]

        # Add is_archived column
        if "is_archived" not in columns:
            await conn.execute(text(
                "ALTER TABLE items ADD COLUMN is_archived BOOLEAN DEFAULT FALSE"
            ))
            print("Added 'is_archived' column")
        else:
            print("Column 'is_archived' already exists, skipping")

        # Add assigned_ak column
        if "assigned_ak" not in columns:
            await conn.execute(text(
                "ALTER TABLE items ADD COLUMN assigned_ak VARCHAR(10)"
            ))
            print("Added 'assigned_ak' column")

            # Migrate existing assigned_ak from metadata to column
            await conn.execute(text("""
                UPDATE items
                SET assigned_ak = metadata #>> '{llm_analysis,assigned_ak}'
                WHERE metadata #>> '{llm_analysis,assigned_ak}' IS NOT NULL
            """))
            print("Migrated assigned_ak values from metadata to column")
        else:
            print("Column 'assigned_ak' already exists, skipping")

        # Add is_manually_reviewed column
        if "is_manually_reviewed" not in columns:
            await conn.execute(text(
                "ALTER TABLE items ADD COLUMN is_manually_reviewed BOOLEAN DEFAULT FALSE"
            ))
            print("Added 'is_manually_reviewed' column")
        else:
            print("Column 'is_manually_reviewed' already exists, skipping")

        # Add reviewed_at column
        if "reviewed_at" not in columns:
            await conn.execute(text(
                "ALTER TABLE items ADD COLUMN reviewed_at TIMESTAMP"
            ))
            print("Added 'reviewed_at' column")
        else:
            print("Column 'reviewed_at' already exists, skipping")

        print("\nMigration complete!")
        print("Items with manual priority/AK changes will now be tracked as training data.")


async def rollback():
    """Remove manual review tracking columns."""
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE items DROP COLUMN IF EXISTS is_archived"))
        await conn.execute(text("ALTER TABLE items DROP COLUMN IF EXISTS assigned_ak"))
        await conn.execute(text("ALTER TABLE items DROP COLUMN IF EXISTS is_manually_reviewed"))
        await conn.execute(text("ALTER TABLE items DROP COLUMN IF EXISTS reviewed_at"))
        print("Successfully dropped manual review tracking columns")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        asyncio.run(rollback())
    else:
        asyncio.run(migrate())
