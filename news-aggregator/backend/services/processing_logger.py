"""Processing analytics logger service.

Records every processing step for items to enable:
- Reproducing how a message ended up with its current priority/classification
- Finding items where classifier and/or LLM were unsure (low confidence)
- Tracking reprocessing events
- Comparing classifier vs LLM decisions
- Training data collection for model improvement
"""

import logging
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import ItemProcessingLog, ProcessingStepType

logger = logging.getLogger(__name__)


def _get_priority_value(priority: Any) -> str | None:
    """Safely get priority value whether it's an enum or string."""
    if priority is None:
        return None
    if hasattr(priority, "value"):
        return priority.value
    return str(priority)


class ProcessingLogger:
    """Logger for tracking item processing steps.

    Usage:
        async with async_session_maker() as db:
            plogger = ProcessingLogger(db)

            # Log a step
            await plogger.log_step(
                step_type=ProcessingStepType.PRE_FILTER,
                item_id=item.id,
                confidence_score=0.75,
                priority_output="medium",
                output_data={"result": ...},
            )

            # Or use context manager for timing
            with plogger.timed_step(ProcessingStepType.LLM_ANALYSIS, item.id) as step:
                result = await processor.analyze(item)
                step.set_output(
                    confidence_score=result.get("relevance_score"),
                    priority_output=result.get("priority"),
                    output_data=result,
                )
    """

    def __init__(
        self,
        session: AsyncSession,
        run_id: str | None = None,
        channel_id: int | None = None,
    ):
        """Initialize the processing logger.

        Args:
            session: Database session for writing logs
            run_id: Optional UUID to link steps across a processing run.
                    If not provided, a new UUID is generated.
            channel_id: Optional channel ID for fetch-level logging
        """
        self.session = session
        self.run_id = run_id or str(uuid4())
        self.channel_id = channel_id
        self._step_order = 0

    def new_item_run(self) -> "ProcessingLogger":
        """Create a new logger with a fresh run_id for a new item.

        Returns:
            New ProcessingLogger instance with unique run_id
        """
        return ProcessingLogger(
            session=self.session,
            run_id=str(uuid4()),
            channel_id=self.channel_id,
        )

    async def log_step(
        self,
        step_type: ProcessingStepType | str,
        item_id: int | None = None,
        *,
        # Timing
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
        # Model info
        model_name: str | None = None,
        model_version: str | None = None,
        model_provider: str | None = None,
        # Scores
        confidence_score: float | None = None,
        priority_input: Any = None,
        priority_output: Any = None,
        ak_suggestions: list[str] | None = None,
        ak_primary: str | None = None,
        ak_confidence: float | None = None,
        relevant: bool | None = None,
        relevance_score: float | None = None,
        # Outcome
        success: bool = True,
        skipped: bool = False,
        skip_reason: str | None = None,
        error_message: str | None = None,
        # Full data
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
    ) -> ItemProcessingLog:
        """Log a processing step.

        Args:
            step_type: Type of processing step (from ProcessingStepType enum)
            item_id: Database ID of the item being processed
            started_at: When the step started
            completed_at: When the step completed
            duration_ms: Processing duration in milliseconds
            model_name: Name of the model used (e.g., "qwen3:14b-q8_0")
            model_version: Version of the model
            model_provider: Provider of the model (e.g., "ollama", "classifier")
            confidence_score: Confidence score from the step (0-1)
            priority_input: Priority before this step
            priority_output: Priority after this step
            ak_suggestions: List of suggested AK codes
            ak_primary: Primary AK suggestion
            ak_confidence: Confidence in AK suggestion
            relevant: Whether item is considered relevant
            relevance_score: Relevance score (0-1)
            success: Whether the step completed successfully
            skipped: Whether the step was skipped
            skip_reason: Reason for skipping (if skipped)
            error_message: Error message (if failed)
            input_data: Full input data (JSON)
            output_data: Full output data (JSON)

        Returns:
            The created ItemProcessingLog record
        """
        self._step_order += 1

        # Convert enums to strings
        step_type_str = step_type.value if hasattr(step_type, "value") else str(step_type)
        priority_input_str = _get_priority_value(priority_input)
        priority_output_str = _get_priority_value(priority_output)

        # Calculate priority_changed
        priority_changed = (
            priority_input_str is not None
            and priority_output_str is not None
            and priority_input_str != priority_output_str
        )

        log_entry = ItemProcessingLog(
            item_id=item_id,
            processing_run_id=self.run_id,
            step_type=step_type_str,
            step_order=self._step_order,
            started_at=started_at or datetime.utcnow(),
            completed_at=completed_at,
            duration_ms=duration_ms,
            model_name=model_name,
            model_version=model_version,
            model_provider=model_provider,
            confidence_score=confidence_score,
            priority_input=priority_input_str,
            priority_output=priority_output_str,
            priority_changed=priority_changed,
            ak_suggestions=ak_suggestions,
            ak_primary=ak_primary,
            ak_confidence=ak_confidence,
            relevant=relevant,
            relevance_score=relevance_score,
            success=success,
            skipped=skipped,
            skip_reason=skip_reason,
            error_message=error_message,
            input_data=input_data,
            output_data=output_data,
        )

        self.session.add(log_entry)
        return log_entry

    @contextmanager
    def timed_step(
        self,
        step_type: ProcessingStepType | str,
        item_id: int | None = None,
    ):
        """Context manager for timing a processing step.

        Usage:
            with plogger.timed_step(ProcessingStepType.LLM_ANALYSIS, item.id) as step:
                result = await processor.analyze(item)
                step.set_output(
                    confidence_score=result.get("relevance_score"),
                    output_data=result,
                )

        Yields:
            A StepContext object for setting output data
        """
        ctx = StepContext(self, step_type, item_id)
        ctx._start()
        try:
            yield ctx
        except Exception as e:
            ctx.set_error(str(e))
            raise
        finally:
            ctx._finish()

    async def log_fetch(
        self,
        channel_id: int,
        items_fetched: int,
        items_new: int,
        error_message: str | None = None,
    ) -> ItemProcessingLog:
        """Log a fetch step for a channel.

        Args:
            channel_id: ID of the channel being fetched
            items_fetched: Total items fetched from source
            items_new: New items (not duplicates)
            error_message: Error message if fetch failed

        Returns:
            The created log entry
        """
        return await self.log_step(
            step_type=ProcessingStepType.FETCH,
            success=error_message is None,
            error_message=error_message,
            output_data={
                "channel_id": channel_id,
                "items_fetched": items_fetched,
                "items_new": items_new,
            },
        )

    async def log_pre_filter(
        self,
        item_id: int,
        result: dict[str, Any],
        priority_input: Any,
        priority_output: Any,
        skip_llm: bool = False,
    ) -> ItemProcessingLog:
        """Log a pre-filter (classifier) step.

        Args:
            item_id: ID of the item being filtered
            result: Classifier result dict
            priority_input: Priority before classification
            priority_output: Priority after classification
            skip_llm: Whether LLM processing will be skipped

        Returns:
            The created log entry
        """
        confidence = result.get("relevance_confidence", 0.5)
        return await self.log_step(
            step_type=ProcessingStepType.PRE_FILTER,
            item_id=item_id,
            model_name="nomic-embed-text-v2",
            model_provider="classifier",
            confidence_score=confidence,
            priority_input=priority_input,
            priority_output=priority_output,
            ak_suggestions=[result.get("ak")] if result.get("ak") else None,
            ak_primary=result.get("ak"),
            ak_confidence=result.get("ak_confidence"),
            relevant=confidence >= 0.25,  # Edge case threshold
            relevance_score=confidence,
            skipped=skip_llm,
            skip_reason="confidence_below_threshold" if skip_llm else None,
            output_data=result,
        )

    async def log_duplicate_check(
        self,
        item_id: int | None,
        is_duplicate: bool,
        similar_to_id: int | None = None,
        similarity_score: float | None = None,
    ) -> ItemProcessingLog:
        """Log a duplicate check step.

        Args:
            item_id: ID of the item being checked (None if not yet created)
            is_duplicate: Whether item is a duplicate
            similar_to_id: ID of the similar item (if found)
            similarity_score: Similarity score (0-1)

        Returns:
            The created log entry
        """
        return await self.log_step(
            step_type=ProcessingStepType.DUPLICATE_CHECK,
            item_id=item_id,
            model_name="paraphrase-mpnet",
            model_provider="classifier",
            confidence_score=similarity_score,
            skipped=is_duplicate,
            skip_reason="semantic_duplicate" if is_duplicate else None,
            output_data={
                "is_duplicate": is_duplicate,
                "similar_to_id": similar_to_id,
                "similarity_score": similarity_score,
            },
        )

    async def log_rule_match(
        self,
        item_id: int,
        rules_matched: list[dict[str, Any]],
        priority_input: Any,
        priority_output: Any,
        keyword_score: int,
    ) -> ItemProcessingLog:
        """Log a rule matching step.

        Args:
            item_id: ID of the item being matched
            rules_matched: List of matched rules with details
            priority_input: Priority before rule matching
            priority_output: Priority after rule matching
            keyword_score: Keyword-based score

        Returns:
            The created log entry
        """
        return await self.log_step(
            step_type=ProcessingStepType.RULE_MATCH,
            item_id=item_id,
            priority_input=priority_input,
            priority_output=priority_output,
            output_data={
                "rules_matched": rules_matched,
                "keyword_score": keyword_score,
            },
        )

    async def log_llm_analysis(
        self,
        item_id: int,
        analysis: dict[str, Any],
        priority_input: Any,
        priority_output: Any,
        duration_ms: int | None = None,
        error_message: str | None = None,
    ) -> ItemProcessingLog:
        """Log an LLM analysis step.

        Args:
            item_id: ID of the item being analyzed
            analysis: Full LLM analysis result
            priority_input: Priority before LLM analysis
            priority_output: Priority after LLM analysis
            duration_ms: Processing time in milliseconds
            error_message: Error message if analysis failed

        Returns:
            The created log entry
        """
        llm_aks = analysis.get("assigned_aks", [])
        return await self.log_step(
            step_type=ProcessingStepType.LLM_ANALYSIS,
            item_id=item_id,
            model_name=settings.ollama_model,
            model_provider="ollama",
            duration_ms=duration_ms,
            confidence_score=analysis.get("relevance_score"),
            priority_input=priority_input,
            priority_output=priority_output,
            ak_suggestions=llm_aks,
            ak_primary=llm_aks[0] if llm_aks else None,
            relevant=analysis.get("relevant"),
            relevance_score=analysis.get("relevance_score"),
            success=error_message is None,
            error_message=error_message,
            output_data=analysis,
        )

    async def log_classifier_worker(
        self,
        item_id: int,
        result: dict[str, Any],
        priority_input: Any,
        priority_output: Any,
    ) -> ItemProcessingLog:
        """Log a classifier worker processing step.

        This is for the background classifier worker that processes
        items without pre_filter metadata.

        Args:
            item_id: ID of the item being classified
            result: Classifier result dict
            priority_input: Priority before classification
            priority_output: Priority after classification

        Returns:
            The created log entry
        """
        confidence = result.get("relevance_confidence", 0.5)
        return await self.log_step(
            step_type=ProcessingStepType.CLASSIFIER_OVERRIDE,
            item_id=item_id,
            model_name="nomic-embed-text-v2",
            model_provider="classifier",
            confidence_score=confidence,
            priority_input=priority_input,
            priority_output=priority_output,
            ak_suggestions=[result.get("ak")] if result.get("ak") else None,
            ak_primary=result.get("ak"),
            ak_confidence=result.get("ak_confidence"),
            relevant=confidence >= 0.25,
            relevance_score=confidence,
            output_data=result,
        )

    async def log_reprocess(
        self,
        item_id: int,
        reason: str,
        trigger: str = "manual",
    ) -> ItemProcessingLog:
        """Log a reprocessing event.

        Args:
            item_id: ID of the item being reprocessed
            reason: Reason for reprocessing
            trigger: What triggered reprocessing (manual, scheduled, etc.)

        Returns:
            The created log entry
        """
        return await self.log_step(
            step_type=ProcessingStepType.REPROCESS,
            item_id=item_id,
            input_data={
                "reason": reason,
                "trigger": trigger,
            },
        )


class StepContext:
    """Context for a timed processing step."""

    def __init__(
        self,
        logger: ProcessingLogger,
        step_type: ProcessingStepType | str,
        item_id: int | None,
    ):
        self._logger = logger
        self._step_type = step_type
        self._item_id = item_id
        self._started_at: datetime | None = None
        self._start_time: float | None = None
        self._kwargs: dict[str, Any] = {}
        self._finished = False

    def _start(self):
        """Record start time."""
        self._started_at = datetime.utcnow()
        self._start_time = time.time()

    def _finish(self):
        """Record end time and create log entry."""
        if self._finished:
            return
        self._finished = True

        elapsed_ms = int((time.time() - self._start_time) * 1000) if self._start_time else None
        completed_at = datetime.utcnow()

        # Schedule the async log call (will be awaited on session flush)
        import asyncio

        asyncio.create_task(
            self._logger.log_step(
                step_type=self._step_type,
                item_id=self._item_id,
                started_at=self._started_at,
                completed_at=completed_at,
                duration_ms=elapsed_ms,
                **self._kwargs,
            )
        )

    def set_output(self, **kwargs):
        """Set output data for the step."""
        self._kwargs.update(kwargs)

    def set_error(self, error_message: str):
        """Mark the step as failed."""
        self._kwargs["success"] = False
        self._kwargs["error_message"] = error_message

    def set_item_id(self, item_id: int):
        """Set the item ID (for steps where item is created during processing)."""
        self._item_id = item_id
