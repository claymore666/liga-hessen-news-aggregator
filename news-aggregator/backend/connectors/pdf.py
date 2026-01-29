"""PDF document connector for extracting text from PDFs."""

import hashlib
import io
from datetime import datetime

import httpx
import pymupdf
from pydantic import BaseModel, Field, HttpUrl

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry


class PDFConfig(BaseModel):
    """Configuration for PDF connector."""

    url: HttpUrl = Field(..., description="Direct PDF URL")
    is_direct_link: bool = Field(
        default=True, description="True if URL points directly to PDF"
    )
    link_selector: str | None = Field(
        default=None, description="CSS selector for PDF links (if is_direct_link=False)"
    )


@ConnectorRegistry.register
class PDFConnector(BaseConnector):
    """PDF document connector.

    Extracts text content from PDF documents using PyMuPDF.
    """

    connector_type = "pdf"
    display_name = "PDF Document"
    description = "Extract text from PDF documents"
    config_schema = PDFConfig

    async def fetch(self, config: PDFConfig) -> list[RawItem]:
        """Fetch and parse PDF document.

        Args:
            config: PDF configuration with URL

        Returns:
            List containing a single RawItem with PDF content
        """
        if not config.is_direct_link:
            # TODO: Implement HTML parsing to find PDF links
            raise NotImplementedError(
                "PDF link extraction from HTML not yet implemented"
            )

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                str(config.url),
                headers={"User-Agent": "NewsAggregator/1.0"},
                follow_redirects=True,
            )
            response.raise_for_status()

        # Parse PDF
        pdf_bytes = io.BytesIO(response.content)
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")

        try:
            # Extract text from all pages
            full_text = ""
            for page in doc:
                full_text += page.get_text()

            # Get metadata
            metadata = doc.metadata or {}
            page_count = len(doc)

            # Use metadata title or first line as title
            title = metadata.get("title", "").strip()
            if not title:
                first_line = full_text.split("\n")[0].strip()[:100]
                title = first_line if first_line else "PDF Document"

            # Generate unique ID from content hash
            external_id = hashlib.md5(response.content).hexdigest()[:16]

            # Get author
            author = metadata.get("author")

            # Try to parse creation date
            published_at = None
            if metadata.get("creationDate"):
                try:
                    # PDF date format: D:YYYYMMDDHHmmSS
                    date_str = metadata["creationDate"]
                    if date_str.startswith("D:"):
                        date_str = date_str[2:16]  # Extract YYYYMMDDHHmmSS
                        published_at = datetime.strptime(date_str, "%Y%m%d%H%M%S")
                except (ValueError, IndexError):
                    pass

        finally:
            doc.close()

        return [
            RawItem(
                external_id=external_id,
                title=title,
                content=full_text,
                url=str(config.url),
                author=author,
                published_at=published_at or datetime.now(),
                metadata={
                    "pages": page_count,
                    "pdf_metadata": {
                        k: v for k, v in metadata.items() if v
                    },
                    "connector": "pdf",
                },
            )
        ]

    async def validate(self, config: PDFConfig) -> tuple[bool, str]:
        """Validate PDF URL.

        Args:
            config: Configuration to validate

        Returns:
            Tuple of (success, message)
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Use HEAD request first to check content type
                response = await client.head(
                    str(config.url),
                    headers={"User-Agent": "NewsAggregator/1.0"},
                    follow_redirects=True,
                )

                content_type = response.headers.get("content-type", "").lower()

                if "pdf" in content_type:
                    return True, "Valid PDF URL"

                # Some servers don't report content-type correctly for HEAD
                # Try to fetch first bytes and check magic number
                response = await client.get(
                    str(config.url),
                    headers={
                        "User-Agent": "NewsAggregator/1.0",
                        "Range": "bytes=0-4",
                    },
                    follow_redirects=True,
                )

                if response.content.startswith(b"%PDF"):
                    return True, "Valid PDF URL"

                return False, f"Not a PDF file (content-type: {content_type})"

        except httpx.TimeoutException:
            return False, "Connection timeout"
        except httpx.HTTPStatusError as e:
            return False, f"HTTP error: {e.response.status_code}"
        except Exception as e:
            return False, f"Error: {str(e)}"
