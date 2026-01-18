"""
Eurostat SDMX metadata service.

Fetches and caches dataset metadata from Eurostat's SDMX API to enrich
RSS feed items with more descriptive information.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# SDMX namespaces
SDMX_NS = {
    "m": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "s": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "c": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
}

# Cache for dataset metadata (refreshed every 24h)
_metadata_cache: dict[str, dict] = {}
_cache_timestamp: Optional[datetime] = None
CACHE_TTL = timedelta(hours=24)


class EurostatMetadata:
    """Service to fetch and provide Eurostat dataset metadata."""

    SDMX_URL = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/dataflow/ESTAT"

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    async def _fetch_all_metadata(self) -> dict[str, dict]:
        """Fetch all dataset metadata from SDMX API."""
        logger.info("Fetching Eurostat SDMX metadata...")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(self.SDMX_URL)
            response.raise_for_status()

        root = ET.fromstring(response.text)
        metadata = {}

        for dataflow in root.findall(".//s:Dataflow", SDMX_NS):
            dataset_id = dataflow.get("id")
            if not dataset_id:
                continue

            # Extract names in different languages
            names = {}
            for name in dataflow.findall("c:Name", SDMX_NS):
                lang = name.get("{http://www.w3.org/XML/1998/namespace}lang", "en")
                names[lang] = name.text

            # Extract useful annotations
            annotations = {}
            for ann in dataflow.findall(".//c:Annotation", SDMX_NS):
                ann_type_el = ann.find("c:AnnotationType", SDMX_NS)
                if ann_type_el is None:
                    continue
                ann_type = ann_type_el.text

                ann_title = ann.find("c:AnnotationTitle", SDMX_NS)
                ann_url = ann.find("c:AnnotationURL", SDMX_NS)

                if ann_type == "ESMS_HTML" and ann_url is not None:
                    annotations["metadata_url"] = ann_url.text
                elif ann_type == "OBS_COUNT" and ann_title is not None:
                    annotations["obs_count"] = ann_title.text
                elif ann_type == "OBS_PERIOD_OVERALL_LATEST" and ann_title is not None:
                    annotations["period_latest"] = ann_title.text
                elif ann_type == "OBS_PERIOD_OVERALL_OLDEST" and ann_title is not None:
                    annotations["period_oldest"] = ann_title.text

            metadata[dataset_id] = {
                "id": dataset_id,
                "names": names,
                **annotations,
            }

        logger.info(f"Loaded metadata for {len(metadata)} Eurostat datasets")
        return metadata

    async def get_metadata(self, dataset_id: str) -> Optional[dict]:
        """
        Get metadata for a specific dataset.

        Args:
            dataset_id: The Eurostat dataset code (e.g., TPS00202)

        Returns:
            Dict with name, metadata_url, obs_count, period info, or None if not found
        """
        global _metadata_cache, _cache_timestamp

        # Check if cache needs refresh
        if (
            _cache_timestamp is None
            or datetime.now() - _cache_timestamp > CACHE_TTL
            or not _metadata_cache
        ):
            try:
                _metadata_cache = await self._fetch_all_metadata()
                _cache_timestamp = datetime.now()
            except Exception as e:
                logger.warning(f"Failed to fetch Eurostat metadata: {e}")
                # Return None but keep old cache if available
                if dataset_id in _metadata_cache:
                    return _metadata_cache.get(dataset_id)
                return None

        return _metadata_cache.get(dataset_id.upper())

    async def enrich_content(
        self, dataset_id: str, original_content: str, lang: str = "en"
    ) -> str:
        """
        Enrich RSS content with SDMX metadata.

        Args:
            dataset_id: The Eurostat dataset code
            original_content: Original RSS description
            lang: Language for the name (en, de, fr)

        Returns:
            Enriched content string
        """
        metadata = await self.get_metadata(dataset_id)
        if not metadata:
            return original_content

        parts = []

        # Add full name
        name = metadata.get("names", {}).get(lang) or metadata.get("names", {}).get(
            "en"
        )
        if name:
            parts.append(f"Dataset: {name}")

        # Add original description if different from name
        if original_content and original_content.lower() != (name or "").lower():
            parts.append(f"Beschreibung: {original_content}")

        # Add data coverage info
        period_info = []
        if metadata.get("period_oldest"):
            period_info.append(f"von {metadata['period_oldest']}")
        if metadata.get("period_latest"):
            period_info.append(f"bis {metadata['period_latest']}")
        if period_info:
            parts.append(f"Zeitraum: {' '.join(period_info)}")

        if metadata.get("obs_count"):
            parts.append(f"Datenpunkte: {metadata['obs_count']}")

        if metadata.get("metadata_url"):
            parts.append(f"Methodische Hinweise: {metadata['metadata_url']}")

        return "\n".join(parts) if parts else original_content


# Singleton instance
_eurostat_service: Optional[EurostatMetadata] = None


def get_eurostat_service() -> EurostatMetadata:
    """Get or create the Eurostat metadata service singleton."""
    global _eurostat_service
    if _eurostat_service is None:
        _eurostat_service = EurostatMetadata()
    return _eurostat_service
