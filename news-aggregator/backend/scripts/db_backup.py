#!/usr/bin/env python3
"""Selective database export for training data and items backup.

This script exports items from the database in various formats suitable for
training ML models or backing up specific data subsets.

Usage:
    # Export all items as JSONL
    python db_backup.py items -o items.jsonl

    # Export items from the last 7 days
    python db_backup.py items --since 2024-01-01 -o recent.jsonl

    # Export only high priority items
    python db_backup.py items --priority high -o important.jsonl

    # Export items from specific connector types
    python db_backup.py items --connector-type rss,x_scraper -o social.jsonl

    # Export without LLM analysis fields
    python db_backup.py items --no-llm -o raw_items.jsonl

    # Export in training format for fine-tuning
    python db_backup.py training -o training_data.jsonl

    # Export training data with custom filters
    python db_backup.py training --min-score 0.7 -o high_quality.jsonl
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import async_session_maker
from models import Item, Channel, Source, Priority

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def export_items(
    output_path: Path,
    since: datetime | None = None,
    until: datetime | None = None,
    priorities: list[str] | None = None,
    connector_types: list[str] | None = None,
    source_ids: list[int] | None = None,
    include_llm: bool = True,
    format: str = "jsonl",
    limit: int | None = None,
) -> int:
    """Export items to JSONL or JSON format.

    Args:
        output_path: Path to output file
        since: Only include items published after this date
        until: Only include items published before this date
        priorities: Filter by priority levels
        connector_types: Filter by connector types
        source_ids: Filter by source IDs
        include_llm: Include LLM analysis fields (summary, detailed_analysis)
        format: Output format (jsonl or json)
        limit: Maximum number of items to export

    Returns:
        Count of exported items.
    """
    async with async_session_maker() as db:
        query = (
            select(Item)
            .options(selectinload(Item.channel).selectinload(Channel.source))
            .order_by(Item.published_at.desc())
        )

        if since:
            query = query.where(Item.published_at >= since)
        if until:
            query = query.where(Item.published_at <= until)
        if priorities:
            try:
                priority_enums = [Priority(p.lower()) for p in priorities]
                query = query.where(Item.priority.in_(priority_enums))
            except ValueError as e:
                logger.error(f"Invalid priority value: {e}")
                return 0
        if connector_types:
            query = query.join(Channel).where(Channel.connector_type.in_(connector_types))
        if source_ids:
            query = query.join(Channel).where(Channel.source_id.in_(source_ids))
        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        items = result.scalars().all()

        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            if format == "json":
                f.write("[\n")

            for i, item in enumerate(items):
                record = {
                    "id": item.id,
                    "external_id": item.external_id,
                    "title": item.title,
                    "content": item.content,
                    "url": item.url,
                    "author": item.author,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "fetched_at": item.fetched_at.isoformat() if item.fetched_at else None,
                    "source_name": item.channel.source.name if item.channel else None,
                    "source_id": item.channel.source_id if item.channel else None,
                    "channel_id": item.channel_id,
                    "connector_type": item.channel.connector_type if item.channel else None,
                    "is_read": item.is_read,
                    "is_starred": item.is_starred,
                }

                if include_llm:
                    record.update({
                        "summary": item.summary,
                        "detailed_analysis": item.detailed_analysis,
                        "priority": item.priority.value if item.priority else None,
                        "priority_score": item.priority_score,
                        "metadata": item.metadata_,
                    })

                if format == "jsonl":
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                else:
                    if i > 0:
                        f.write(",\n")
                    f.write(json.dumps(record, ensure_ascii=False, indent=2))

                count += 1

            if format == "json":
                f.write("\n]")

        return count


async def export_training_data(
    output_path: Path,
    min_score: float | None = None,
    since: datetime | None = None,
    limit: int | None = None,
) -> int:
    """Export items in training data format for LLM fine-tuning.

    Format matches the relevance-tuner expected input format.

    Args:
        output_path: Path to output file
        min_score: Only include items with relevance_score >= min_score
        since: Only include items published after this date
        limit: Maximum number of items to export

    Returns:
        Count of exported training records.
    """
    async with async_session_maker() as db:
        # Only export items that have been processed by LLM (have summary)
        query = (
            select(Item)
            .options(selectinload(Item.channel).selectinload(Channel.source))
            .where(Item.summary.isnot(None))
            .order_by(Item.published_at.desc())
        )

        if since:
            query = query.where(Item.published_at >= since)
        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        items = result.scalars().all()

        count = 0
        skipped = 0

        with open(output_path, "w", encoding="utf-8") as f:
            for item in items:
                # Get LLM analysis from metadata
                llm_analysis = item.metadata_.get("llm_analysis", {}) if item.metadata_ else {}
                relevance_score = llm_analysis.get("relevance_score", 0)

                # Filter by minimum score if specified
                if min_score is not None and relevance_score < min_score:
                    skipped += 1
                    continue

                # Build training record in expected format
                record = {
                    "input": {
                        "titel": item.title,
                        "inhalt": item.content[:2000] if item.content else "",
                        "quelle": item.channel.source.name if item.channel else "Unbekannt",
                        "datum": item.published_at.strftime("%Y-%m-%d") if item.published_at else None,
                    },
                    "output": {
                        "summary": item.summary,
                        "detailed_analysis": item.detailed_analysis,
                        "relevant": relevance_score > 0.5 if relevance_score else False,
                        "relevance_score": relevance_score,
                        "priority": item.priority.value if item.priority else "low",
                        "assigned_ak": llm_analysis.get("assigned_ak"),
                        "tags": llm_analysis.get("tags", []),
                        "reasoning": llm_analysis.get("reasoning"),
                    },
                    "metadata": {
                        "item_id": item.id,
                        "source_name": item.channel.source.name if item.channel else None,
                        "connector_type": item.channel.connector_type if item.channel else None,
                        "url": item.url,
                    },
                }

                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

        if skipped > 0:
            logger.info(f"Skipped {skipped} items with relevance_score < {min_score}")

        return count


async def export_stats() -> dict:
    """Get export statistics without writing files."""
    async with async_session_maker() as db:
        from sqlalchemy import func

        total_items = await db.scalar(select(func.count(Item.id))) or 0
        items_with_summary = await db.scalar(
            select(func.count(Item.id)).where(Item.summary.isnot(None))
        ) or 0
        items_without_summary = total_items - items_with_summary

        # Count by priority
        priority_counts = {}
        for priority in Priority:
            count = await db.scalar(
                select(func.count(Item.id)).where(Item.priority == priority)
            ) or 0
            priority_counts[priority.value] = count

        # Count by connector type
        connector_counts = {}
        result = await db.execute(
            select(Channel.connector_type, func.count(Item.id))
            .join(Item)
            .group_by(Channel.connector_type)
        )
        for row in result:
            connector_counts[row[0]] = row[1]

        return {
            "total_items": total_items,
            "items_with_summary": items_with_summary,
            "items_without_summary": items_without_summary,
            "by_priority": priority_counts,
            "by_connector_type": connector_counts,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Database export utility for training data and backups",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Items export subcommand
    items_parser = subparsers.add_parser("items", help="Export items to JSONL/JSON")
    items_parser.add_argument("--output", "-o", required=True, help="Output file path")
    items_parser.add_argument("--since", help="Export items since date (YYYY-MM-DD)")
    items_parser.add_argument("--until", help="Export items until date (YYYY-MM-DD)")
    items_parser.add_argument("--priority", help="Filter by priorities (comma-separated: none,low,medium,high)")
    items_parser.add_argument("--connector-type", help="Filter by connector types (comma-separated)")
    items_parser.add_argument("--source-id", help="Filter by source IDs (comma-separated)")
    items_parser.add_argument("--no-llm", action="store_true", help="Exclude LLM analysis fields")
    items_parser.add_argument("--format", choices=["jsonl", "json"], default="jsonl", help="Output format")
    items_parser.add_argument("--limit", type=int, help="Maximum number of items to export")

    # Training data export subcommand
    training_parser = subparsers.add_parser("training", help="Export training data for fine-tuning")
    training_parser.add_argument("--output", "-o", required=True, help="Output file path")
    training_parser.add_argument("--min-score", type=float, help="Minimum relevance score to include")
    training_parser.add_argument("--since", help="Export items since date (YYYY-MM-DD)")
    training_parser.add_argument("--limit", type=int, help="Maximum number of items to export")

    # Stats subcommand
    stats_parser = subparsers.add_parser("stats", help="Show export statistics")

    args = parser.parse_args()

    if args.command == "items":
        count = asyncio.run(export_items(
            output_path=Path(args.output),
            since=datetime.fromisoformat(args.since) if args.since else None,
            until=datetime.fromisoformat(args.until) if args.until else None,
            priorities=args.priority.split(",") if args.priority else None,
            connector_types=args.connector_type.split(",") if args.connector_type else None,
            source_ids=[int(x) for x in args.source_id.split(",")] if args.source_id else None,
            include_llm=not args.no_llm,
            format=args.format,
            limit=args.limit,
        ))
        logger.info(f"Exported {count} items to {args.output}")

    elif args.command == "training":
        count = asyncio.run(export_training_data(
            output_path=Path(args.output),
            min_score=args.min_score,
            since=datetime.fromisoformat(args.since) if args.since else None,
            limit=args.limit,
        ))
        logger.info(f"Exported {count} training records to {args.output}")

    elif args.command == "stats":
        stats = asyncio.run(export_stats())
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
