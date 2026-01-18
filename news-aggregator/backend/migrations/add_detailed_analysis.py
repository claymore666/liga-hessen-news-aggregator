"""Add detailed_analysis column to items table.

This migration adds a new field for storing the LLM-generated detailed analysis
(5-8 sentences) separately from the short summary (2-3 sentences).

Run with: python migrations/add_detailed_analysis.py
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from database import engine


async def migrate():
    """Add detailed_analysis column to items table."""
    async with engine.begin() as conn:
        # Check if column already exists
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'items' AND column_name = 'detailed_analysis'
        """))
        if result.fetchone():
            print("Column 'detailed_analysis' already exists, skipping migration")
            return

        # Add the new column
        await conn.execute(text(
            "ALTER TABLE items ADD COLUMN detailed_analysis TEXT"
        ))
        print("Successfully added 'detailed_analysis' column to items table")


async def rollback():
    """Remove detailed_analysis column."""
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE items DROP COLUMN IF EXISTS detailed_analysis"))
        print("Successfully dropped 'detailed_analysis' column")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        asyncio.run(rollback())
    else:
        asyncio.run(migrate())
