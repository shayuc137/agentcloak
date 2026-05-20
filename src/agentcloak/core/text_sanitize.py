"""Terminal-output sanitization.

Console log text comes straight from the page — an attacker-controlled
``console.log`` can embed ANSI escape sequences to spoof terminal output,
move the cursor, or clear the screen when an agent's transcript is rendered
in a terminal (the classic "terminal injection" vector). We strip every
escape sequence and C0/C1 control character before the text leaves the
browser layer, keeping only the whitespace an agent legitimately needs to
read multi-line log output (``\\n`` and ``\\t``).

This lives in ``core`` so console capture today and any future surface that
echoes page-controlled strings (download filenames, dialog messages, …) can
share one implementation.
"""

from __future__ import annotations

import re

__all__ = ["sanitize_terminal_text"]

# CSI / OSC / single-character escape sequences. ``\x1b`` (ESC) followed by:
#   * ``[`` … final byte in 0x40-0x7E  → CSI (colors, cursor moves, clears)
#   * ``]`` … BEL or ESC-\\            → OSC (window title, hyperlinks)
#   * a single byte in 0x40-0x5F       → two-char escapes (e.g. ESC c reset)
_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC terminated by BEL or ST
    r"|\x1b[@-Z\\-_]"  # two-character escape
)

# C0 controls (0x00-0x1F) and DEL/C1 (0x7F-0x9F) minus the whitespace we keep.
# \x09 = tab, \x0a = newline. Everything else in the range is stripped.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def sanitize_terminal_text(text: str) -> str:
    """Strip ANSI escapes and control characters, preserving ``\\n``/``\\t``.

    Returns the cleaned string. ``None``-safe callers should guard upstream;
    this expects a real ``str``.
    """
    if not text:
        return text
    cleaned = _ANSI_RE.sub("", text)
    return _CONTROL_RE.sub("", cleaned)
