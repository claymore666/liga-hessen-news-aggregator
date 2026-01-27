"""Admin endpoints for housekeeping and data management."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Item, Priority, Setting

logger = logging.getLogger(__name__)
router = APIRouter()


# Default retention periods in days
DEFAULT_HOUSEKEEPING_CONFIG = {
    "retention_days_high": 365,
    "retention_days_medium": 180,
    "retention_days_low": 90,
    "retention_days_none": 30,
    "autopurge_enabled": False,
    "exclude_starred": True,
}


class HousekeepingConfig(BaseModel):
    """Housekeeping configuration."""
    retention_days_high: int = 365
    retention_days_medium: int = 180
    retention_days_low: int = 90
    retention_days_none: int = 30
    autopurge_enabled: bool = False
    exclude_starred: bool = True


class CleanupPreview(BaseModel):
    """Preview of items to be deleted."""
    total: int
    by_priority: dict[str, int]
    oldest_item_date: str | None


class CleanupResult(BaseModel):
    """Result of cleanup operation."""
    deleted: int
    by_priority: dict[str, int]


class StorageStats(BaseModel):
    """Storage usage statistics."""
    postgresql_size_bytes: int
    postgresql_size_human: str
    postgresql_items: int = Field(description="Total items in PostgreSQL database")
    postgresql_duplicates: int = Field(description="Items marked as duplicates (similar_to_id set)")
    search_index_size_bytes: int = Field(description="Disk size of semantic search index (nomic embeddings)")
    search_index_size_human: str
    search_index_items: int = Field(description="Items indexed for semantic search")
    duplicate_index_size_bytes: int = Field(description="Disk size of duplicate detection index (paraphrase embeddings)")
    duplicate_index_size_human: str
    duplicate_index_items: int = Field(description="Items indexed for duplicate detection")
    total_size_bytes: int
    total_size_human: str


class VectorSyncPreview(BaseModel):
    """Preview of vector index sync operation."""
    orphaned_ids: int = Field(description="IDs in vector index but not in database")
    missing_ids: int = Field(description="IDs in database but not in vector index")


class VectorSyncResult(BaseModel):
    """Result of vector index sync operation."""
    orphaned_deleted: int = Field(description="Orphaned IDs removed from vector index")
    missing_indexed: int = Field(description="Missing IDs added to vector index")


def _format_bytes(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


async def get_housekeeping_config(db: AsyncSession) -> dict:
    """Load housekeeping config from database."""
    result = await db.execute(
        select(Setting).where(Setting.key == "housekeeping")
    )
    setting = result.scalar_one_or_none()

    if setting and setting.value:
        # Merge with defaults to handle new fields
        config = dict(DEFAULT_HOUSEKEEPING_CONFIG)
        config.update(setting.value)
        return config
    return dict(DEFAULT_HOUSEKEEPING_CONFIG)


async def save_housekeeping_config(db: AsyncSession, config: HousekeepingConfig) -> dict:
    """Save housekeeping config to database."""
    result = await db.execute(
        select(Setting).where(Setting.key == "housekeeping")
    )
    setting = result.scalar_one_or_none()

    config_dict = config.model_dump()

    if setting:
        setting.value = config_dict
    else:
        new_setting = Setting(
            key="housekeeping",
            value=config_dict,
            description="Housekeeping and retention configuration",
        )
        db.add(new_setting)

    await db.commit()
    return config_dict


async def get_items_to_delete(
    db: AsyncSession,
    config: dict,
    execute: bool = False,
) -> tuple[int, dict[str, int], str | None, list[int]]:
    """Get or delete items based on retention config.

    Args:
        db: Database session
        config: Housekeeping configuration
        execute: If True, delete the items; if False, just count them

    Returns:
        Tuple of (total_count, by_priority_counts, oldest_date, deleted_ids)
        deleted_ids is only populated when execute=True
    """
    now = datetime.utcnow()
    by_priority: dict[str, int] = {}
    total = 0
    oldest_date: datetime | None = None
    all_deleted_ids: list[int] = []

    exclude_starred = config.get("exclude_starred", True)

    # Process each priority level
    for priority, days_key in [
        (Priority.HIGH, "retention_days_high"),
        (Priority.MEDIUM, "retention_days_medium"),
        (Priority.LOW, "retention_days_low"),
        (Priority.NONE, "retention_days_none"),
    ]:
        retention_days = config.get(days_key, 30)
        cutoff = now - timedelta(days=retention_days)

        # Build the where clause
        conditions = [
            Item.priority == priority,
            Item.fetched_at < cutoff,
        ]
        if exclude_starred:
            conditions.append(Item.is_starred == False)  # noqa: E712

        if execute:
            # First, collect the IDs to delete (needed for vector index cleanup)
            id_stmt = select(Item.id).where(and_(*conditions))
            result = await db.execute(id_stmt)
            ids_to_delete = [row[0] for row in result.fetchall()]

            if ids_to_delete:
                # Delete items
                stmt = delete(Item).where(Item.id.in_(ids_to_delete))
                await db.execute(stmt)
                count = len(ids_to_delete)
                all_deleted_ids.extend(ids_to_delete)
            else:
                count = 0
        else:
            # Count items
            count_stmt = select(func.count(Item.id)).where(and_(*conditions))
            count = await db.scalar(count_stmt) or 0

            # Get oldest date for this priority
            if count > 0:
                oldest_stmt = (
                    select(func.min(Item.fetched_at))
                    .where(and_(*conditions))
                )
                priority_oldest = await db.scalar(oldest_stmt)
                if priority_oldest and (oldest_date is None or priority_oldest < oldest_date):
                    oldest_date = priority_oldest

        if count > 0:
            by_priority[priority.value] = count
            total += count

    return total, by_priority, oldest_date.isoformat() if oldest_date else None, all_deleted_ids


@router.get("/admin/housekeeping", response_model=HousekeepingConfig)
async def get_housekeeping(
    db: AsyncSession = Depends(get_db),
) -> HousekeepingConfig:
    """Get current housekeeping configuration."""
    config = await get_housekeeping_config(db)
    return HousekeepingConfig(**config)


@router.put("/admin/housekeeping", response_model=HousekeepingConfig)
async def update_housekeeping(
    config: HousekeepingConfig,
    db: AsyncSession = Depends(get_db),
) -> HousekeepingConfig:
    """Update housekeeping configuration."""
    saved = await save_housekeeping_config(db, config)
    logger.info(f"Housekeeping config updated: {saved}")
    return HousekeepingConfig(**saved)


@router.post("/admin/housekeeping/preview", response_model=CleanupPreview)
async def preview_cleanup(
    db: AsyncSession = Depends(get_db),
) -> CleanupPreview:
    """Preview items that would be deleted based on current retention settings."""
    config = await get_housekeeping_config(db)
    total, by_priority, oldest_date, _ = await get_items_to_delete(db, config, execute=False)

    return CleanupPreview(
        total=total,
        by_priority=by_priority,
        oldest_item_date=oldest_date,
    )


@router.post("/admin/housekeeping/cleanup", response_model=CleanupResult)
async def execute_cleanup(
    db: AsyncSession = Depends(get_db),
) -> CleanupResult:
    """Execute cleanup based on current retention settings.

    Deletes items from both PostgreSQL and vector indexes (search + duplicate).
    """
    from services.relevance_filter import create_relevance_filter

    config = await get_housekeeping_config(db)
    total, by_priority, _, deleted_ids = await get_items_to_delete(db, config, execute=True)

    # Clean up vector indexes
    if deleted_ids:
        try:
            relevance_filter = await create_relevance_filter()
            if relevance_filter:
                # Convert int IDs to strings for the classifier API
                str_ids = [str(id) for id in deleted_ids]
                deleted_search, deleted_dup = await relevance_filter.delete_items(str_ids)
                logger.info(
                    f"Vector index cleanup: {deleted_search} from search, "
                    f"{deleted_dup} from duplicate index"
                )
        except Exception as e:
            logger.warning(f"Failed to clean up vector indexes: {e}")

    await db.commit()

    logger.info(f"Housekeeping cleanup completed: deleted {total} items ({by_priority})")
    return CleanupResult(
        deleted=total,
        by_priority=by_priority,
    )


@router.get("/admin/storage", response_model=StorageStats)
async def get_storage_stats(
    db: AsyncSession = Depends(get_db),
) -> StorageStats:
    """Get storage usage statistics."""
    from services.relevance_filter import create_relevance_filter

    # Get PostgreSQL database size
    pg_size_result = await db.execute(
        select(func.pg_database_size(func.current_database()))
    )
    pg_size_bytes = pg_size_result.scalar() or 0

    # Get item count
    items_count = await db.scalar(select(func.count(Item.id))) or 0

    # Get duplicate count (items with similar_to_id set)
    duplicates_count = await db.scalar(
        select(func.count(Item.id)).where(Item.similar_to_id.isnot(None))
    ) or 0

    # Get classifier storage stats
    search_size_bytes = 0
    search_items = 0
    duplicate_size_bytes = 0
    duplicate_items = 0

    try:
        relevance_filter = await create_relevance_filter()
        if relevance_filter:
            storage_stats = await relevance_filter.get_storage_stats()
            if storage_stats:
                search_size_bytes = storage_stats.get("search_index_size_bytes", 0)
                search_items = storage_stats.get("search_index_items", 0)
                duplicate_size_bytes = storage_stats.get("duplicate_index_size_bytes", 0)
                duplicate_items = storage_stats.get("duplicate_index_items", 0)
    except Exception as e:
        logger.warning(f"Failed to get classifier storage stats: {e}")

    total_size = pg_size_bytes + search_size_bytes + duplicate_size_bytes

    return StorageStats(
        postgresql_size_bytes=pg_size_bytes,
        postgresql_size_human=_format_bytes(pg_size_bytes),
        postgresql_items=items_count,
        postgresql_duplicates=duplicates_count,
        search_index_size_bytes=search_size_bytes,
        search_index_size_human=_format_bytes(search_size_bytes),
        search_index_items=search_items,
        duplicate_index_size_bytes=duplicate_size_bytes,
        duplicate_index_size_human=_format_bytes(duplicate_size_bytes),
        duplicate_index_items=duplicate_items,
        total_size_bytes=total_size,
        total_size_human=_format_bytes(total_size),
    )


@router.post("/admin/housekeeping/vector-sync/preview", response_model=VectorSyncPreview)
async def preview_vector_sync(
    db: AsyncSession = Depends(get_db),
) -> VectorSyncPreview:
    """Preview vector index sync - shows orphaned and missing IDs.

    Orphaned: IDs in vector index that no longer exist in database (will be deleted)
    Missing: IDs in database that are not in vector index (will be indexed)
    """
    from services.relevance_filter import create_relevance_filter

    relevance_filter = await create_relevance_filter()
    if not relevance_filter:
        return VectorSyncPreview(orphaned_ids=0, missing_ids=0)

    # Get all IDs from vector index
    indexed_ids = await relevance_filter.get_all_indexed_ids()
    indexed_set = set(indexed_ids)

    # Get all IDs from database (only items that should be indexed)
    result = await db.execute(select(Item.id))
    db_ids = {str(row[0]) for row in result.fetchall()}

    # Find orphaned (in vector but not in DB)
    orphaned = indexed_set - db_ids

    # Find missing (in DB but not in vector)
    missing = db_ids - indexed_set

    return VectorSyncPreview(
        orphaned_ids=len(orphaned),
        missing_ids=len(missing),
    )


@router.post("/admin/housekeeping/vector-sync", response_model=VectorSyncResult)
async def execute_vector_sync(
    db: AsyncSession = Depends(get_db),
) -> VectorSyncResult:
    """Sync vector index with database.

    - Removes orphaned entries (IDs in vector index but not in database)
    - Does NOT re-index missing items (too expensive, handled by classifier worker)
    """
    from services.relevance_filter import create_relevance_filter

    relevance_filter = await create_relevance_filter()
    if not relevance_filter:
        return VectorSyncResult(orphaned_deleted=0, missing_indexed=0)

    # Get all IDs from vector index
    indexed_ids = await relevance_filter.get_all_indexed_ids()
    indexed_set = set(indexed_ids)

    # Get all IDs from database
    result = await db.execute(select(Item.id))
    db_ids = {str(row[0]) for row in result.fetchall()}

    # Find orphaned (in vector but not in DB)
    orphaned = list(indexed_set - db_ids)

    # Delete orphaned entries
    orphaned_deleted = 0
    if orphaned:
        deleted_search, deleted_dup = await relevance_filter.delete_items(orphaned)
        orphaned_deleted = max(deleted_search, deleted_dup)
        logger.info(f"Vector sync: deleted {orphaned_deleted} orphaned entries")

    return VectorSyncResult(
        orphaned_deleted=orphaned_deleted,
        missing_indexed=0,  # Not implemented - too expensive
    )
