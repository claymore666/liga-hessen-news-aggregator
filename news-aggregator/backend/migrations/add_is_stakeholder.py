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
        result = await conn.execute(text("PRAGMA table_info(sources)"))
        columns = [row[1] for row in result.fetchall()]

        if "is_stakeholder" in columns:
            print("Column 'is_stakeholder' already exists, skipping.")
            return True

        # Add the new column with default value
        print("Adding 'is_stakeholder' column...")
        await conn.execute(
            text("ALTER TABLE sources ADD COLUMN is_stakeholder BOOLEAN DEFAULT 0")
        )
        print("Column added.")

        print("\n✅ Migration completed successfully!")
        print("\nTo mark sources as stakeholders, use:")
        print("  curl -X PATCH http://localhost:8000/api/sources/{id} \\")
        print("    -H 'Content-Type: application/json' \\")
        print("    -d '{\"is_stakeholder\": true}'")
        return True


if __name__ == "__main__":
    success = asyncio.run(migrate())
    sys.exit(0 if success else 1)
