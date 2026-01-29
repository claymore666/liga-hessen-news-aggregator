"""Admin API endpoints package.

This package contains admin endpoints split by concern:
- items: Item deletion, reanalysis, classification
- health: System health checks
- stats: Database and system statistics
- logs: Application log viewing
- workers: Scheduler and worker control
- housekeeping: Data retention and cleanup
- gpu1: GPU1 power management status
"""

from fastapi import APIRouter

from . import gpu1, health, housekeeping, items, logs, stats, workers

# Re-export commonly used items from submodules
from .housekeeping import (
    DEFAULT_HOUSEKEEPING_CONFIG,
    get_housekeeping_config,
    get_items_to_delete,
)

# Aggregate all admin routers into one
router = APIRouter()

router.include_router(items.router)
router.include_router(health.router)
router.include_router(stats.router)
router.include_router(logs.router)
router.include_router(workers.router)
router.include_router(housekeeping.router)
router.include_router(gpu1.router)

__all__ = [
    "router",
    "gpu1",
    "health",
    "housekeeping",
    "items",
    "logs",
    "stats",
    "workers",
    # Re-exported from housekeeping
    "DEFAULT_HOUSEKEEPING_CONFIG",
    "get_housekeeping_config",
    "get_items_to_delete",
]
