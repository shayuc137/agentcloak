"""Resolve screenshot encoding without producing mislabeled image files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentcloak.core.errors import BackendError

__all__ = ["ScreenshotFormatResolution", "resolve_screenshot_format"]

_SUFFIX_FORMATS = {".jpeg": "jpeg", ".jpg": "jpeg", ".png": "png"}
_VALID_FORMATS = frozenset({"jpeg", "png"})


@dataclass(frozen=True)
class ScreenshotFormatResolution:
    """Carry the selected encoding and any suffix fallback context."""

    format: str | None
    unrecognized_suffix: str = ""

    def warning_for(self, resolved_format: str) -> str:
        """Explain a non-silent fallback once the real default is known."""
        if not self.unrecognized_suffix:
            return ""
        return (
            f"unrecognized screenshot suffix '{self.unrecognized_suffix}'; "
            f"used configured format '{resolved_format}'"
        )


def resolve_screenshot_format(
    *,
    explicit_format: str | None,
    output_path: str | Path | None,
    default_format: str | None,
) -> ScreenshotFormatResolution:
    """Resolve explicit format, recognized suffix, then configured default."""
    explicit = explicit_format.strip().lower() if explicit_format else ""
    if explicit and explicit not in _VALID_FORMATS:
        raise BackendError(
            error="invalid_screenshot_format",
            hint=f"Unsupported screenshot format '{explicit_format}'",
            action="use jpeg or png",
        )

    suffix = Path(output_path).suffix.lower() if output_path else ""
    inferred = _SUFFIX_FORMATS.get(suffix)
    if explicit and inferred and explicit != inferred:
        raise BackendError(
            error="screenshot_format_suffix_conflict",
            hint=(
                f"Screenshot format '{explicit}' conflicts with output suffix "
                f"'{suffix}'"
            ),
            action="make --format agree with the output suffix, or omit --format",
        )
    if explicit:
        return ScreenshotFormatResolution(format=explicit)
    if inferred:
        return ScreenshotFormatResolution(format=inferred)

    default = default_format.strip().lower() if default_format else ""
    if default and default not in _VALID_FORMATS:
        raise BackendError(
            error="invalid_screenshot_format",
            hint=f"Unsupported configured screenshot format '{default_format}'",
            action="set browser.screenshot_format to jpeg or png",
        )
    return ScreenshotFormatResolution(
        format=default or None,
        unrecognized_suffix=suffix,
    )
