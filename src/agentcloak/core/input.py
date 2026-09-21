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


def validate_dpr(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise invalid_input("DPR must be a finite positive number")
    return value


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
    names = [
        "Enter",
        "Escape",
        "Tab",
        "Space",
        "Backspace",
        "Delete",
        "Insert",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "CapsLock",
        "NumLock",
        "ScrollLock",
        "Pause",
        "PrintScreen",
        "ContextMenu",
        "AltGraph",
        "ControlLeft",
        "ControlRight",
        "ShiftLeft",
        "ShiftRight",
        "AltLeft",
        "AltRight",
        "MetaLeft",
        "MetaRight",
        "NumpadAdd",
        "NumpadSubtract",
        "NumpadMultiply",
        "NumpadDivide",
        "NumpadDecimal",
        "NumpadEnter",
        "Backquote",
        "Minus",
        "Equal",
        "BracketLeft",
        "BracketRight",
        "Backslash",
        "Semicolon",
        "Quote",
        "Comma",
        "Period",
        "Slash",
    ]
    names += [f"F{i}" for i in range(1, 13)]
    names += [f"{prefix}{i}" for prefix in ("Digit", "Numpad") for i in range(10)]
    names += [f"Key{letter}" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    aliases.update({name.lower(): name for name in names})
    aliases.update({"esc": "Escape", "return": "Enter", "spacebar": "Space"})
    # A trailing plus is the literal '+' key, including in 'Control++'.
    parts = key.split("+")
    if key == "+" or key.endswith("++"):
        parts = [*parts[:-2], "+"]
    modifiers = {"Control", "Alt", "Shift", "Meta", "ControlOrMeta"}
    aliases["controlormeta"] = "ControlOrMeta"
    normalized = [aliases.get(part.lower(), part) for part in parts]
    literal = normalized[-1] if normalized else ""
    printable = len(literal) == 1 and (" " <= literal <= "~" or literal in {"\r", "\n"})
    if (
        not normalized
        or any(part not in modifiers for part in normalized[:-1])
        or not normalized[-1]
        or (not printable and normalized[-1] not in {*names, *modifiers})
    ):
        raise invalid_input(f"Unknown key combination: {key!r}")
    return "+".join(normalized)
