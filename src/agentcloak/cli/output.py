"""CLI output primitives — text-first, ``--json`` opt-in.

Design background
-----------------
Pre-v0.3.0 every CLI command emitted a full ``{ok, seq, data}`` JSON envelope
to stdout. Agents had to pipe through ``jq`` to extract useful fields, which
burned tokens on parsing instructions and made the CLI feel "JSON-y" rather
than Unix-y.

This module mirrors pinchtab's :file:`output/format.go` five-primitive model:

============  ================  =========================================
primitive     stream            purpose
============  ================  =========================================
``success``   stdout            no-payload confirmation ("OK" by default)
``value``     stdout            useful result (URL, snapshot tree, …)
``info``      stderr            human progress / hint (agents may ignore)
``error``     stderr + exit 1   failure with hint + recovery action
``json_out``  stdout            full envelope when ``--json`` is active
============  ================  =========================================

Mode selection
--------------
:func:`set_json_mode` flips a module-level switch read by every command.
The flag is set in :func:`agentcloak.cli.app.main` after extracting either
``--json`` from ``argv`` or the ``AGENTCLOAK_OUTPUT=json`` env var. Once
enabled, commands route everything through :func:`json_out` for backward
compatibility with scripts that expect the envelope.

Why module-level state instead of a typer dependency? Click parses flags per
sub-command and we need every nested ``typer.Typer`` group to see ``--json``
in any position. The cleanest way is to strip the flag from ``sys.argv``
in :func:`_extract_global_flags` before Typer dispatches, then have commands
read the state directly.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

import orjson

if TYPE_CHECKING:
    from agentcloak.core.errors import AgentBrowserError

__all__ = [
    "_detect_env_json_mode",
    "error",
    "error_from_exception",
    "info",
    "is_json_mode",
    "json_out",
    "set_json_mode",
    "set_pretty",
    "success",
    "value",
]


# Module-level state — set once during ``main()`` then read by every command.
_pretty = False
_json_mode = False


def set_pretty(*, enabled: bool) -> None:
    """Toggle indented JSON output (``--pretty`` flag, only meaningful in JSON mode)."""
    global _pretty
    _pretty = enabled


def set_json_mode(*, enabled: bool) -> None:
    """Toggle the global ``--json`` mode.

    Resolution order (handled by :func:`agentcloak.cli.app.main`):
    1. ``--json`` flag on argv
    2. ``AGENTCLOAK_OUTPUT=json`` env var
    3. default ``False`` (text mode)
    """
    global _json_mode
    _json_mode = enabled


def is_json_mode() -> bool:
    """Return ``True`` when the CLI should emit full JSON envelopes."""
    return _json_mode


# ---------------------------------------------------------------------------
# Text-mode primitives
# ---------------------------------------------------------------------------


def success(msg: str = "OK") -> None:
    """Write a one-line success acknowledgement to stdout.

    Used by no-payload actions (``daemon stop``, ``capture clear``, …) so the
    caller sees a deterministic token rather than nothing.
    """
    sys.stdout.write(msg)
    if not msg.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def value(v: Any) -> None:
    """Write the useful payload to stdout.

    Strings / multi-line text are written verbatim. Anything else falls back
    to ``orjson`` so list/dict results stay readable when ``--json`` was not
    requested (e.g. a daemon route forgot to render text). A trailing newline
    is appended when missing so shells can pipe cleanly.
    """
    if isinstance(v, str):
        text = v
    elif isinstance(v, bytes):
        text = v.decode("utf-8", errors="replace")
    else:
        # Non-string fallbacks — pretty so humans can read them when running
        # ad-hoc, but still on stdout because the caller asked for a value.
        text = orjson.dumps(v, option=orjson.OPT_INDENT_2).decode()
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def info(text: str) -> None:
    """Write a hint / progress line to stderr.

    Agents normally ignore stderr — this channel is for humans glancing at
    the terminal. Keep it short and don't depend on it being captured.
    """
    sys.stderr.write(text)
    if not text.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.flush()


def error(
    hint: str,
    action: str = "",
    *,
    exit_code: int = 1,
    code: str = "command_failed",
    data: dict[str, Any] | None = None,
) -> None:
    """Emit a failure in the selected format and always exit nonzero."""
    if _json_mode:
        _write_envelope(
            {
                "ok": False,
                "error": {"code": code, "message": hint},
                "hint": hint,
                "action": action,
                **({"data": data} if data is not None else {}),
            }
        )
    sys.stderr.write(f"Error [{code}]: {hint}\n")
    if action:
        sys.stderr.write(f"  -> {action}\n")
    sys.stderr.flush()
    raise SystemExit(exit_code or 1)


def error_from_exception(exc: AgentBrowserError) -> None:
    """Render domain failures through the same exit path as local failures."""
    error(exc.hint or exc.error, exc.action, code=exc.error)


# ---------------------------------------------------------------------------
# JSON-mode primitive
# ---------------------------------------------------------------------------


def json_out(data: dict[str, Any], *, seq: int = 0) -> None:
    """Emit the full ``{ok, seq, data}`` envelope to stdout (``--json`` mode).

    Kept for parity with the pre-v0.3.0 contract — see
    :file:`.trellis/spec/cli/cli-output-contract.md` for the precise shape.
    """
    envelope: dict[str, Any] = {"ok": True, "seq": seq, "data": data}
    _write_envelope(envelope)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _write_envelope(envelope: dict[str, Any]) -> None:
    opts = orjson.OPT_INDENT_2 if _pretty else 0
    sys.stdout.buffer.write(orjson.dumps(envelope, option=opts))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def _detect_env_json_mode() -> bool:
    """Read ``AGENTCLOAK_OUTPUT=json`` env var (escape hatch for CI/scripts)."""
    return os.environ.get("AGENTCLOAK_OUTPUT", "").strip().lower() == "json"
