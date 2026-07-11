"""Helpers for turning CDP exception details into bounded agent diagnostics."""

from __future__ import annotations

from typing import Any, cast

__all__ = ["format_cdp_exception"]

_MAX_DIAGNOSTIC_CHARS = 400
_TRUNCATED_MARKER = " ... [truncated]"
_GENERIC_TEXT = {"uncaught", "uncaught (in promise)", "script error"}


def format_cdp_exception(details: dict[str, Any]) -> str:
    """Return the thrown message plus its first useful CDP source location."""
    exception = details.get("exception")
    exception_obj: dict[str, Any] = (
        cast("dict[str, Any]", exception) if isinstance(exception, dict) else {}
    )

    description = _string_value(exception_obj.get("description"))
    value = _string_value(exception_obj.get("value"))
    raw_text = _string_value(details.get("text"))
    text = "" if _is_generic_text(raw_text) else raw_text

    message = description or value or text or "JS exception"
    location = _first_location(details)
    if location and location not in message:
        message = f"{message}\n  at {location}"
    return _truncate(message)


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool | int | float):
        return str(value)
    return ""


def _is_generic_text(value: str) -> bool:
    normalized = value.strip().rstrip(".").lower()
    return normalized in _GENERIC_TEXT


def _first_location(details: dict[str, Any]) -> str:
    stack_trace = details.get("stackTrace")
    if isinstance(stack_trace, dict):
        stack_trace_obj = cast("dict[str, Any]", stack_trace)
        call_frames = stack_trace_obj.get("callFrames")
        if isinstance(call_frames, list) and call_frames:
            frames = cast("list[Any]", call_frames)
            first = frames[0]
            if isinstance(first, dict):
                location = _format_location(cast("dict[str, Any]", first))
                if location:
                    return location
    return _format_location(details)


def _format_location(data: dict[str, Any]) -> str:
    url = _string_value(data.get("url"))
    line = data.get("lineNumber")
    column = data.get("columnNumber")
    if not url and not isinstance(line, int) and not isinstance(column, int):
        return ""

    location = url or "<anonymous>"
    if isinstance(line, int):
        location += f":{line + 1}"
        if isinstance(column, int):
            location += f":{column + 1}"
    return location


def _truncate(value: str) -> str:
    if len(value) <= _MAX_DIAGNOSTIC_CHARS:
        return value
    keep = _MAX_DIAGNOSTIC_CHARS - len(_TRUNCATED_MARKER)
    return value[:keep].rstrip() + _TRUNCATED_MARKER
