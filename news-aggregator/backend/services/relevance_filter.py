"""
Relevance pre-filter using embedding classifier.
Calls the GPU-accelerated classifier service on gpu1.
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class RelevanceFilter:
    """
    Pre-filter for news articles using embedding classifier.

    Calls the classifier API on gpu1 to quickly determine relevance
    before expensive LLM processing.
    """

    def __init__(self, base_url: str, threshold: float = 0.8, timeout: int = 30):
        """
        Initialize the relevance filter.

        Args:
            base_url: Classifier API URL (e.g., http://gpu1:8081)
            threshold: Confidence threshold for filtering (default: 0.8)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.threshold = threshold
        self.timeout = timeout

    async def classify(
        self,
        title: str,
        content: str,
        source: str = "",
    ) -> dict:
        """
        Classify an article for relevance.

        Returns:
            dict with keys: relevant, relevance_confidence, priority, ak, etc.

        Raises:
            httpx.RequestError: If the classifier service is unavailable
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/classify",
                json={
                    "title": title,
                    "content": content,
                    "source": source,
                },
            )
            response.raise_for_status()
            return response.json()

    async def should_process(
        self,
        title: str,
        content: str,
        source: str = "",
    ) -> tuple[bool, Optional[dict]]:
        """
        Determine if an article should be processed by LLM.

        Returns:
            tuple of (should_process: bool, classification: dict or None)
            - If relevant or uncertain: (True, classification_result)
            - If clearly irrelevant: (False, classification_result)
        """
        try:
            result = await self.classify(title, content, source)

            # If clearly irrelevant (high confidence), skip LLM
            if not result["relevant"] and result["relevance_confidence"] < (1 - self.threshold):
                logger.info(
                    f"Pre-filtered as irrelevant: {title[:50]}... "
                    f"(confidence: {1 - result['relevance_confidence']:.1%})"
                )
                return False, result

            return True, result

        except httpx.RequestError as e:
            # If classifier unavailable, process anyway (fail open)
            logger.warning(f"Classifier unavailable, processing anyway: {e}")
            return True, None

    async def is_available(self) -> bool:
        """Check if the classifier service is available."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception as e:
            logger.debug(f"Classifier not available: {e}")
            return False

    async def get_health(self) -> Optional[dict]:
        """Get classifier health info."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/health")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.debug(f"Failed to get classifier health: {e}")
            return None


async def create_relevance_filter() -> Optional[RelevanceFilter]:
    """
    Create a RelevanceFilter instance from settings.

    Returns None if classifier is disabled or unavailable.
    """
    from config import settings

    if not settings.classifier_url:
        logger.info("Classifier URL not configured, pre-filtering disabled")
        return None

    filter_instance = RelevanceFilter(
        base_url=settings.classifier_url,
        threshold=settings.classifier_threshold,
    )

    # Check availability
    if await filter_instance.is_available():
        health = await filter_instance.get_health()
        logger.info(f"Relevance filter enabled: {health}")
        return filter_instance
    else:
        logger.warning("Classifier service unavailable, pre-filtering disabled")
        return None
