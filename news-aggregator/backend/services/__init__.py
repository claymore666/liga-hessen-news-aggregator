"""Services package."""

from services.pipeline import Pipeline, RawItem, process_items
from services.processor import ItemProcessor, create_processor_from_settings
from services.scheduler import (
    fetch_all_channels,
    fetch_channel,
    fetch_due_channels,
    get_job_status,
    start_scheduler,
    stop_scheduler,
    trigger_channel_fetch,
)

__all__ = [
    # Pipeline
    "Pipeline",
    "RawItem",
    "process_items",
    # Processor (LLM-based)
    "ItemProcessor",
    "create_processor_from_settings",
    # Scheduler
    "fetch_all_channels",
    "fetch_channel",
    "fetch_due_channels",
    "get_job_status",
    "start_scheduler",
    "stop_scheduler",
    "trigger_channel_fetch",
]
