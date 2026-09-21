"""Input parsing shared by browser backends and CLI surfaces."""

from __future__ import annotations

import math
import re

from agentcloak.core.errors import AgentBrowserError


def invalid_input(message: str) -> AgentBrowserError:
    return AgentBrowserError(
        error="invalid_argument", hint=message, action="check command arguments"
    )


def parse_ref(value: str) -> int:
    match = re.fullmatch(r"(?:([0-9]+)|\[([0-9]+)\])", value)
    if not match:
        raise invalid_input("Element reference must be N or '[N]'")
    return int(match.group(1) or match.group(2))


def parse_point(value: str) -> tuple[float, float]:
    try:
        parts = value.split(",")
        if len(parts) != 2:
            raise ValueError
        x, y = map(float, parts)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError
        return x, y
    except ValueError as exc:
        raise invalid_input("Coordinates must be two finite numbers: x,y") from exc


def parse_viewport(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([0-9]+)[xX]([0-9]+)", value)
    if not match:
        raise invalid_input("Viewport must be WIDTHxHEIGHT")
    width, height = map(int, match.groups())
    if not (1 <= width <= 16384 and 1 <= height <= 16384):
        raise invalid_input("Viewport dimensions must be between 1 and 16384")
    return width, height


def normalize_key(key: str) -> str:
    aliases = {
        "ctrl": "Control",
        "control": "Control",
        "cmd": "Meta",
        "command": "Meta",
        "meta": "Meta",
        "opt": "Alt",
        "option": "Alt",
        "alt": "Alt",
        "shift": "Shift",
    }
    return "+".join(aliases.get(part.lower(), part) for part in key.split("+"))
