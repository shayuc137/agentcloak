"""Pydantic models for download routes (7a R2).

Two download modes share these models: direct-URL download (daemon fetches the
URL server-side with the browser's cookies) and click-triggered download
(Playwright ``page.on('download')`` / CDP ``Page.downloadWillBegin``). Both end
up as a :class:`DownloadEntry` recorded in the session list.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "DownloadEntryModel",
    "DownloadListResponse",
    "DownloadResponse",
    "DownloadUrlRequest",
    "DownloadWaitClickRequest",
    "DownloadWaitRequest",
]


class DownloadUrlRequest(BaseModel):
    """Direct-URL download. The SSRF guard rejects private/loopback targets."""

    url: str = Field(description="http(s) URL to download server-side.")
    output_dir: str | None = Field(
        None,
        description="Directory to save into. Defaults to the system temp dir.",
    )


class DownloadWaitRequest(BaseModel):
    """Wait for the next click-triggered download to finish and save it."""

    output_dir: str | None = Field(
        None,
        description="Directory to save into. Defaults to the system temp dir.",
    )
    timeout: float | None = Field(
        None,
        description="Seconds to wait for a download. Defaults to navigation_timeout.",
    )


class DownloadWaitClickRequest(BaseModel):
    """Atomic click-then-download: arm waiter, click [index], await download."""

    index: int = Field(description="Element [N] to click (the download trigger).")
    output_dir: str | None = Field(
        None,
        description="Directory to save into. Defaults to the system temp dir.",
    )
    timeout: float | None = Field(
        None,
        description="Seconds to wait for a download. Defaults to navigation_timeout.",
    )
    force: bool = Field(
        False, description="Pass force=True to the click (skip pointer check)."
    )


class DownloadEntryModel(BaseModel):
    """A completed download saved to local disk."""

    filename: str = Field(description="Saved file name.")
    path: str = Field(description="Absolute path on the daemon host.")
    size: int = Field(description="File size in bytes.")
    url: str = Field("", description="Source URL the file came from.")
    source: str = Field(
        "url", description="'url' (direct httpx) or 'event' (click-triggered)."
    )


class DownloadResponse(DownloadEntryModel):
    """Single-download result (url / wait). Inherits the entry fields."""


class DownloadListResponse(BaseModel):
    """All downloads saved during this daemon session."""

    downloads: list[DownloadEntryModel] = Field(
        description="Completed downloads in arrival order."
    )
    count: int = Field(0, description="Number of downloads.")
