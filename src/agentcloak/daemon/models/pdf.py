"""Pydantic model for the PDF-export route (7a R6).

``page.pdf()`` (CDP ``Page.printToPDF``) renders the current page to PDF bytes;
only headless Chromium supports it. Like screenshot, the route either writes
the bytes to ``output_path`` or returns them base64-encoded.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["PdfRequest", "PdfResponse"]


class PdfRequest(BaseModel):
    """PDF export options. Mirrors the Playwright/CDP option vocabulary."""

    format: str = Field("A4", description="Paper format: A4, Letter, Legal, etc.")
    landscape: bool = Field(False, description="Landscape orientation.")
    scale: float | None = Field(
        None, description="Render scale (0.1-2.0). Unset uses the browser default."
    )
    margin: dict[str, Any] | None = Field(
        None,
        description="Margins, e.g. {'top': '1cm', 'bottom': '1cm'}.",
    )
    page_ranges: str | None = Field(
        None, description="Pages to print, e.g. '1-3,5'. Empty = all pages."
    )
    output_path: str | None = Field(
        None,
        description="Save the PDF here. Omit to return base64-encoded bytes.",
    )


class PdfResponse(BaseModel):
    """PDF export result.

    When ``output_path`` was set the response carries ``path`` + ``size``;
    otherwise it carries ``base64`` + ``size`` (mirrors the screenshot route).
    """

    model_config = ConfigDict(extra="allow")

    size: int = Field(0, description="PDF size in bytes.")
