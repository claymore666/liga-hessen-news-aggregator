"""Migration: Add is_stakeholder column to sources table.

Sources marked as stakeholder will never be filtered out by the LLM,
keeping all their messages for training data while still categorizing them.

Run with: python migrations/add_is_stakeholder.py
"""

import asyncio
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from database import engine


async def migrate():
    """Add is_stakeholder column to sources table."""
    async with engine.begin() as conn:
        # Check if column already exists
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'sources' AND column_name = 'is_stakeholder'
        """))

        if result.fetchone():
            print("Column 'is_stakeholder' already exists, skipping.")
            return True

        # Add the new column with default value
        print("Adding 'is_stakeholder' column...")
        await conn.execute(
            text("ALTER TABLE sources ADD COLUMN is_stakeholder BOOLEAN DEFAULT FALSE")
        )
        print("Column added.")

        print("\n✅ Migration completed successfully!")
        print("\nTo mark sources as stakeholders, use:")
        print("  curl -X PATCH http://localhost:8000/api/sources/{id} \\")
        print("    -H 'Content-Type: application/json' \\")
        print("    -d '{\"is_stakeholder\": true}'")
        return True


async def rollback():
    """Remove is_stakeholder column."""
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE sources DROP COLUMN IF EXISTS is_stakeholder"))
        print("Successfully dropped 'is_stakeholder' column")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        asyncio.run(rollback())
    else:
        success = asyncio.run(migrate())
        sys.exit(0 if success else 1)
