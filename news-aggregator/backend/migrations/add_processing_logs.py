"""Add item_processing_logs table for analytics.

This migration creates the processing logs table used to track
every step of item evaluation for analytics and debugging.

Run with: python migrations/add_processing_logs.py
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import inspect, text
from database import engine


async def migrate():
    """Create item_processing_logs table if it doesn't exist."""
    async with engine.begin() as conn:
        # Check if table already exists
        def check_table_exists(sync_conn):
            inspector = inspect(sync_conn)
            return "item_processing_logs" in inspector.get_table_names()

        exists = await conn.run_sync(check_table_exists)

        if exists:
            print("Table 'item_processing_logs' already exists, skipping migration")
            return

        # Create the table
        await conn.execute(text("""
            CREATE TABLE item_processing_logs (
                id SERIAL PRIMARY KEY,
                item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
                processing_run_id VARCHAR(36) NOT NULL,

                step_type VARCHAR(50) NOT NULL,
                step_order INTEGER NOT NULL,

                started_at TIMESTAMP NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMP,
                duration_ms INTEGER,

                model_name VARCHAR(100),
                model_version VARCHAR(50),
                model_provider VARCHAR(50),

                confidence_score FLOAT,
                priority_input VARCHAR(20),
                priority_output VARCHAR(20),
                priority_changed BOOLEAN DEFAULT FALSE,
                ak_suggestions JSON,
                ak_primary VARCHAR(10),
                ak_confidence FLOAT,
                relevant BOOLEAN,
                relevance_score FLOAT,

                success BOOLEAN NOT NULL DEFAULT TRUE,
                skipped BOOLEAN DEFAULT FALSE,
                skip_reason VARCHAR(100),
                error_message TEXT,

                input_data JSON,
                output_data JSON,

                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))

        print("Successfully created 'item_processing_logs' table")

        # Create indexes
        indexes = [
            ("ix_processing_logs_item_id", "CREATE INDEX ix_processing_logs_item_id ON item_processing_logs(item_id)"),
            ("ix_processing_logs_run_id", "CREATE INDEX ix_processing_logs_run_id ON item_processing_logs(processing_run_id)"),
            ("ix_processing_logs_step_type", "CREATE INDEX ix_processing_logs_step_type ON item_processing_logs(step_type)"),
            ("ix_processing_logs_created_at", "CREATE INDEX ix_processing_logs_created_at ON item_processing_logs(created_at)"),
        ]

        for name, sql in indexes:
            await conn.execute(text(sql))
            print(f"Created index: {name}")

        # Partial indexes for efficient common queries
        partial_indexes = [
            (
                "ix_processing_logs_low_confidence",
                """CREATE INDEX ix_processing_logs_low_confidence
                   ON item_processing_logs(step_type, confidence_score)
                   WHERE confidence_score IS NOT NULL AND confidence_score < 0.5"""
            ),
            (
                "ix_processing_logs_priority_changed",
                """CREATE INDEX ix_processing_logs_priority_changed
                   ON item_processing_logs(step_type, priority_changed)
                   WHERE priority_changed = TRUE"""
            ),
        ]

        for name, sql in partial_indexes:
            await conn.execute(text(sql))
            print(f"Created partial index: {name}")

        print("Migration completed successfully!")


async def rollback():
    """Drop the item_processing_logs table."""
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS item_processing_logs CASCADE"))
        print("Successfully dropped 'item_processing_logs' table")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        asyncio.run(rollback())
    else:
        asyncio.run(migrate())
