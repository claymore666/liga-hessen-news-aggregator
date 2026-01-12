"""Add needs_llm_processing column to items table.

This migration adds a new field for tracking items that need LLM processing
due to GPU unavailability during initial fetch.

Run with: python migrations/add_needs_llm_processing.py
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from database import engine


async def migrate():
    """Add needs_llm_processing column to items table."""
    async with engine.begin() as conn:
        # Check if column already exists
        result = await conn.execute(text("PRAGMA table_info(items)"))
        columns = [row[1] for row in result.fetchall()]

        if "needs_llm_processing" in columns:
            print("Column 'needs_llm_processing' already exists, skipping migration")
            return

        # Add the new column with default False
        await conn.execute(text(
            "ALTER TABLE items ADD COLUMN needs_llm_processing BOOLEAN DEFAULT 0"
        ))
        print("Successfully added 'needs_llm_processing' column to items table")

        # Create index for efficient querying
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_items_needs_llm_processing ON items (needs_llm_processing)"
        ))
        print("Successfully created index on 'needs_llm_processing' column")


async def rollback():
    """Remove needs_llm_processing column (SQLite doesn't support DROP COLUMN directly)."""
    print("Note: SQLite doesn't support DROP COLUMN. To rollback, recreate the table.")
    print("This is a non-destructive migration, rollback is typically not needed.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        asyncio.run(rollback())
    else:
        asyncio.run(migrate())
