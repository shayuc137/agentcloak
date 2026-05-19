"""Text renderers shared by CLI and MCP — agent-first one-liner output.

Why a shared core module?
-------------------------
The CLI default mode and the MCP tool surface both speak to AI agents.
Returning raw JSON wastes tokens (key names, quotes, braces), and having
two different render paths invites format drift between surfaces. v0.3.x
collapses the rendering into one module under :mod:`agentcloak.core` so
both surfaces produce byte-identical text for the same daemon payload.

Consumers
---------
* :mod:`agentcloak.cli._dispatch` — receives JSON from the daemon, calls
  the matching ``render_xxx_text`` on the inner ``data`` dict, prints to
  stdout. ``--json`` mode skips the renderer and emits the envelope verbatim.
* :mod:`agentcloak.mcp._format` — same call, but returns the rendered string
  to FastMCP. Screenshot responses bypass this and return ``ImageContent``
  directly (multimodal LLMs read the image, not the metadata).

Renderer contract
-----------------
Each ``render_xxx_text(data)`` consumes a *route's success payload* (the
inner ``data`` dict, not the full envelope) and returns a plain string.
Renderers must be pure: they may read from ``data`` but must not mutate it,
log anything, or touch global state. Each renderer is unit-testable in
isolation, which is the main reason they all live in one module rather than
being scattered across the call sites.
"""

from __future__ import annotations

from typing import Any, cast

import orjson


def _as_dict(value: Any) -> dict[str, Any]:
    """Narrow a runtime-checked dict to ``dict[str, Any]`` for type checking.

    Pyright in strict mode infers ``isinstance(x, dict)`` as
    ``dict[Unknown, Unknown]`` — useless for downstream ``.get()`` calls.
    We've already verified the runtime type, so :func:`cast` is the right
    escape hatch and stays cheap (no copy).
    """
    return cast("dict[str, Any]", value)


__all__ = [
    "render_action_text",
    "render_batch_text",
    "render_bridge_claim_text",
    "render_bridge_finalize_text",
    "render_capture_analyze_text",
    "render_capture_clear_text",
    "render_capture_export_text",
    "render_capture_status_text",
    "render_cdp_endpoint_text",
    "render_cookies_export_text",
    "render_cookies_import_text",
    "render_dialog_handle_text",
    "render_dialog_status_text",
    "render_evaluate_text",
    "render_fetch_text",
    "render_frame_focus_text",
    "render_frame_list_text",
    "render_health_text",
    "render_launch_text",
    "render_navigate_text",
    "render_network_text",
    "render_profile_create_from_current_text",
    "render_profile_create_text",
    "render_profile_delete_text",
    "render_profile_list_text",
    "render_resume_text",
    "render_screenshot_text",
    "render_shutdown_text",
    "render_snapshot_text",
    "render_spell_list_text",
    "render_spell_run_text",
    "render_tab_list_text",
    "render_tab_op_text",
    "render_token_text",
    "render_upload_text",
    "render_wait_text",
]


# Title-line truncation budget. Long page titles still happen (e.g. e-commerce
# breadcrumb lists) and uncapped output is hostile to grep/awk pipelines.
_TITLE_MAX = 80


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------


def _clip_title(title: str) -> str:
    """Trim a page title to ``_TITLE_MAX`` characters and replace pipes.

    The ``|`` separator is used in our header lines (``# title | url | …``);
    raw pipes inside the title break shell parsers that try to split. Use the
    full-width equivalent so the title still reads naturally to humans.
    """
    cleaned = (title or "").replace("|", "｜")  # noqa: RUF001 — full-width pipe by design
    if len(cleaned) > _TITLE_MAX:
        return cleaned[: _TITLE_MAX - 1] + "…"
    return cleaned


def _format_feedback(data: dict[str, Any]) -> list[str]:
    """Collect proactive-feedback lines from an action/navigate response.

    Returns one suffix string per non-empty feedback channel. Empty when no
    feedback fields are present so the caller can keep one-liner output.
    """
    lines: list[str] = []
    raw_nav = data.get("navigation")
    if isinstance(raw_nav, dict):
        nav = _as_dict(raw_nav)
        url = str(nav.get("url", "") or "")
        if url:
            lines.append(f"navigation: {url}")
    pending = data.get("pending_requests")
    if isinstance(pending, int) and pending > 0:
        lines.append(f"pending_requests: {pending}")
    elif isinstance(pending, list):
        # Length is what the renderer needs; type doesn't matter.
        pending_list = cast("list[Any]", pending)
        if pending_list:
            lines.append(f"pending_requests: {len(pending_list)}")
    raw_dialog = data.get("dialog")
    if isinstance(raw_dialog, dict):
        dialog = _as_dict(raw_dialog)
        dtype = str(dialog.get("type", "") or "dialog")
        msg = str(dialog.get("message", "") or "")
        lines.append(f"dialog: {dtype} {msg!r}" if msg else f"dialog: {dtype}")
    raw_download = data.get("download")
    if isinstance(raw_download, dict) and raw_download:
        download = _as_dict(raw_download)
        name = str(download.get("suggested_filename", "") or "?")
        lines.append(f"download: {name}")
    current = data.get("current_value")
    if current is not None and current != "":
        lines.append(f"current_value: {current!r}")
    if data.get("caused_navigation") and not any(
        line.startswith("navigation:") for line in lines
    ):
        # Already captured via ``navigation`` above when the URL is known;
        # only emit the bare hint if nothing else explained the change.
        lines.append("caused_navigation: true")
    return lines


def _attach_feedback(base: str, data: dict[str, Any]) -> str:
    """Append proactive feedback to a one-line summary.

    Single hint stays inline (``clicked [7] | navigation: https://...``).
    Multiple hints split to indented lines so wrapping stays predictable.
    """
    feedback = _format_feedback(data)
    if not feedback:
        return base
    if len(feedback) == 1:
        return f"{base} | {feedback[0]}"
    indent = "\n  "
    return base + indent + indent.join(feedback)


# ---------------------------------------------------------------------------
# Navigation / observation
# ---------------------------------------------------------------------------


def _format_tok_estimate(tree_text: str) -> str:
    """Render a coarse token estimate from raw ``tree_text`` length.

    Uses ``len // 4`` because that's the standard OpenAI/Anthropic
    rule-of-thumb for English-heavy text and avoids pulling in tiktoken
    (heavy dependency, big install footprint). The estimate is intentionally
    a hint, not a budget: agents use it to decide "do I need to page with
    --offset?" — they don't feed it back into a precise token-budget
    calculation. Sub-1K trees print as ``~123 tok`` to avoid the ``~0.1K``
    rounding noise; 1K+ collapses to ``~1.8K tok`` so the header stays narrow.
    """
    chars = len(tree_text)
    tokens = chars // 4
    if tokens < 1000:
        return f"~{tokens} tok"
    return f"~{tokens / 1000:.1f}K tok"


def _render_snapshot_header(data: dict[str, Any]) -> str:
    """Build the ``# title | url | N nodes (M interactive) | seq=K`` header line.

    Shared between :func:`render_snapshot_text` (the dedicated route) and the
    ``--snap`` paths in :func:`render_navigate_text` / :func:`render_action_text`
    so all three produce the same machine-parseable header. Diff counts and
    ``showing 1-N`` truncation suffix are appended when present in ``data``,
    and the trailing ``~NK tok`` is a chars/4 estimate of the rendered tree
    so agents can budget context without a tokenizer dependency.
    """
    title = _clip_title(str(data.get("title", "") or ""))
    url = str(data.get("url", "") or "")
    total_nodes = int(data.get("total_nodes", 0) or 0)
    interactive = int(data.get("total_interactive", 0) or 0)
    seq = int(data.get("seq", 0) or 0)
    diff_info = ""
    if data.get("diff"):
        raw_counts = data.get("diff_counts")
        counts = _as_dict(raw_counts) if isinstance(raw_counts, dict) else {}
        added = int(counts.get("added", 0) or 0)
        changed = int(counts.get("changed", 0) or 0)
        removed = int(counts.get("removed", 0) or 0)
        if counts and (added or changed or removed):
            diff_info = f" | diff: +{added} ~{changed} -{removed}"
        elif counts:
            # Counts dict present but all zero → diff ran and found nothing.
            diff_info = " | (no changes)"
        else:
            # Backward compat: route omitted counts → just mark diff active.
            diff_info = " | diff"
    truncated_at = data.get("truncated_at")
    showing = ""
    if truncated_at:
        showing = f" | showing 1-{int(truncated_at)}"

    tree_text = str(data.get("tree_text", "") or "")
    tok = f" | {_format_tok_estimate(tree_text)}"

    return (
        f"# {title} | {url} | {total_nodes} nodes "
        f"({interactive} interactive) | seq={seq}{diff_info}{showing}{tok}"
    )


def _render_snapshot_block(snap: dict[str, Any]) -> str:
    """Render a snapshot dict as ``<header>\\n<tree>`` with optional truncation tail.

    Used by ``--snap`` paths to embed a snapshot tree under a navigate/action
    one-liner. Returns an empty string when there's no ``tree_text`` so the
    caller can keep the header-only path.
    """
    tree = str(snap.get("tree_text", "") or "")
    if not tree:
        return ""
    header = _render_snapshot_header(snap)
    body = f"{header}\n{tree}".rstrip("\n")
    truncated_at = snap.get("truncated_at")
    if truncated_at and not _tree_has_inline_truncation(tree):
        total_nodes = int(snap.get("total_nodes", 0) or 0)
        body += (
            f"\n--- {int(truncated_at)}/{total_nodes} nodes shown. "
            f"Continue with --offset {int(truncated_at)} ---"
        )
    return body


def render_navigate_text(data: dict[str, Any]) -> str:
    """Render the ``/navigate`` payload as ``url | title``.

    When ``include_snapshot`` was requested the snapshot block (header +
    tree) follows after a blank line so callers get a single combined block —
    matches the ``--snap`` combo flag on action commands.
    """
    url = str(data.get("url", "") or "")
    title = _clip_title(str(data.get("title", "") or ""))
    header = f"{url} | {title}" if title else url
    raw_snap = data.get("snapshot")
    if isinstance(raw_snap, dict):
        block = _render_snapshot_block(_as_dict(raw_snap))
        if block:
            return f"{header}\n\n{block}"
    return header


def render_snapshot_text(data: dict[str, Any]) -> str:
    """Render the ``/snapshot`` payload with a metadata header line.

    Format::

        # <title> | <url> | <N> nodes (<M> interactive) | seq=N
        <tree_text>

    When the daemon truncated the tree, the renderer appends a trailing
    ``--- shown N of total. Continue with --offset N ---`` so the agent
    knows how to page forward — but only when ``build_snapshot`` didn't
    already emit its own ``--- not shown: ... ---`` summary inside the tree
    (which it does whenever ``max_nodes`` truncation hits). Otherwise we'd
    print two adjacent truncation lines that say the same thing.
    """
    header = _render_snapshot_header(data)
    tree = str(data.get("tree_text", "") or "")
    body = f"{header}\n{tree}".rstrip("\n")
    truncated_at = data.get("truncated_at")
    if truncated_at and not _tree_has_inline_truncation(tree):
        total_nodes = int(data.get("total_nodes", 0) or 0)
        body += (
            f"\n--- {int(truncated_at)}/{total_nodes} nodes shown. "
            f"Continue with --offset {int(truncated_at)} ---"
        )
    return body


def _tree_has_inline_truncation(tree: str) -> bool:
    """Return True when ``tree_text`` already ends with a ``--- not shown ---`` summary.

    ``build_snapshot``/``truncate_diff_lines`` append their own summary line
    when a node-level cap clipped the output. The header-level
    ``--- N/total shown ---`` is a fallback meant for the JSON-only cases
    where the inline summary isn't emitted (e.g. char-level truncation that
    only sets ``truncated_at`` without a summary line). Detecting either
    inline marker prevents the double-summary regression.
    """
    if not tree:
        return False
    # Iterate the last few non-blank lines; the summary line is always at the
    # tail but a trailing newline may push it off the strict last position.
    for line in reversed(tree.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith("--- not shown") or stripped.startswith(
            "--- truncated"
        )
    return False


def render_screenshot_text(data: dict[str, Any]) -> str:
    """Render the ``/screenshot`` payload as a saved-file path or short summary.

    The route returns base64 — turning that into a temp file lives in the
    CLI command (it has access to the user's filesystem). When the route
    response only carries ``size`` + ``format`` we still produce a useful
    one-liner so curl users see something meaningful.
    """
    size = int(data.get("size", 0) or 0)
    fmt = str(data.get("format", "") or "")
    return f"screenshot captured | {size} bytes | format={fmt}"


def render_evaluate_text(data: dict[str, Any]) -> str:
    """Render the ``/evaluate`` payload following R10 rules.

    * scalars (str/number/bool) → raw value
    * null / undefined → empty
    * object/array → pretty JSON so agents can read it
    * truncated marker → forward verbatim so the agent knows
    """
    if data.get("truncated"):
        result_text = str(data.get("result", "") or "")
        size = int(data.get("total_size", 0) or 0)
        return f"{result_text}\n--- truncated at {size} bytes ---"
    result = data.get("result")
    if result is None:
        return ""
    if isinstance(result, str | int | float | bool):
        return str(result)
    return orjson.dumps(result, option=orjson.OPT_INDENT_2).decode()


def render_network_text(data: dict[str, Any]) -> str:
    """Render the ``/network`` payload as ``method status url`` per line."""
    requests: list[Any] = list(data.get("requests") or [])
    if not requests:
        return "no network requests"
    lines: list[str] = []
    for raw in requests:
        if not isinstance(raw, dict):
            continue
        req = _as_dict(raw)
        method = str(req.get("method", "") or "GET")
        url = str(req.get("url", "") or "")
        status = req.get("status")
        if status is None:
            lines.append(f"{method:6s} ---  {url}")
        else:
            lines.append(f"{method:6s} {int(status):3d}  {url}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def render_action_text(kind: str, target: str, data: dict[str, Any]) -> str:
    """Render an action response as ``<verb> [<target>]`` plus optional feedback.

    The verb table mirrors the action ``kind`` enum so ``fill`` stays
    ``filled``. When the route attached a snapshot (``--snap`` combo flag)
    the tree text follows after a blank line.
    """
    verb_table = {
        "click": "clicked",
        "fill": "filled",
        "type": "typed",
        "press": "pressed",
        "scroll": "scrolled",
        "hover": "hovered",
        "select": "selected",
        "keydown": "keydown",
        "keyup": "keyup",
    }
    verb = verb_table.get(kind, kind)
    ref = f"[{target}]" if target and target.lstrip("-").isdigit() else target
    base = f"{verb} {ref}".rstrip()
    if kind == "fill" and "text" in data:
        text = str(data.get("text", ""))
        base = f"{base} | value: {text!r}"
    elif kind in ("press", "keydown", "keyup") and not ref:
        # Press without a target focuses the page; surface the key name so the
        # one-liner still says what happened.
        key = str(data.get("key", "") or "")
        base = f"{verb} {key}".rstrip()
    result = _attach_feedback(base, data)
    raw_snap = data.get("snapshot")
    if isinstance(raw_snap, dict):
        block = _render_snapshot_block(_as_dict(raw_snap))
        if block:
            return f"{result}\n\n{block}"
    return result


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


def render_tab_list_text(data: dict[str, Any]) -> str:
    """Render ``/tabs`` as a git-branch style listing.

    ``*`` marks the active tab. Format keeps tab id and url on the same line
    so awk pipelines can grab columns easily.
    """
    tabs: list[Any] = list(data.get("tabs") or [])
    if not tabs:
        return "no open tabs"
    lines: list[str] = []
    for raw in tabs:
        if not isinstance(raw, dict):
            continue
        tab = _as_dict(raw)
        marker = "*" if tab.get("active") else " "
        tab_id = tab.get("tab_id", "?")
        url = str(tab.get("url", "") or "")
        title = _clip_title(str(tab.get("title", "") or ""))
        line = f"{marker} {tab_id}  {url}"
        if title:
            line = f"{line}  | {title}"
        lines.append(line)
    return "\n".join(lines)


def render_tab_op_text(verb: str, data: dict[str, Any]) -> str:
    """Render a tab new/close/switch response with the relevant identifier."""
    tab_id = data.get("tab_id", "?")
    url = str(data.get("url", "") or "")
    title = _clip_title(str(data.get("title", "") or ""))
    suffix = ""
    if url:
        suffix = f" | {url}"
    elif title:
        suffix = f" | {title}"
    return f"{verb} tab {tab_id}{suffix}"


# ---------------------------------------------------------------------------
# Daemon lifecycle / health
# ---------------------------------------------------------------------------


def render_health_text(data: dict[str, Any]) -> str:
    """Render ``/health`` as a one-liner with optional URL/capture suffix."""
    tier = str(data.get("stealth_tier", data.get("active_tier", "?")) or "?")
    browser_ready = data.get("browser_ready")
    seq = int(data.get("seq", 0) or 0)
    status = "ready" if browser_ready else "not-ready"
    parts = [f"tier: {tier}", f"browser: {status}", f"seq: {seq}"]
    current_url = data.get("current_url")
    if current_url:
        parts.append(f"url: {current_url}")
    if data.get("capture_recording"):
        parts.append(f"capture: recording ({int(data.get('capture_entries', 0) or 0)})")
    return " | ".join(parts)


def render_launch_text(data: dict[str, Any]) -> str:
    """Render ``/launch`` as ``switched to <tier> | browser: <status>``."""
    tier = str(data.get("active_tier", "?") or "?")
    ready = data.get("browser_ready")
    status = "ready" if ready else "pending"
    profile = data.get("profile")
    base = f"switched to {tier} | browser: {status}"
    if profile:
        base = f"{base} | profile: {profile}"
    return base


# ---------------------------------------------------------------------------
# Spell
# ---------------------------------------------------------------------------


def render_spell_list_text(data: dict[str, Any]) -> str:
    """Render ``/spell/list`` as one ``name | strategy | description`` per line."""
    spells: list[Any] = list(data.get("spells") or [])
    if not spells:
        return "no spells registered"
    lines: list[str] = []
    for raw in spells:
        if not isinstance(raw, dict):
            continue
        spell = _as_dict(raw)
        name = str(spell.get("full_name", "") or "")
        strategy = str(spell.get("strategy", "") or "")
        desc = str(spell.get("description", "") or "")
        lines.append(f"{name} | {strategy} | {desc}")
    return "\n".join(lines)


def render_spell_run_text(data: dict[str, Any]) -> str:
    """Render ``/spell/run`` by emitting the inner ``result`` verbatim.

    Pipelines often return list[dict]; we pretty-print them so a caller can
    still pipe to ``less`` and read the output. Scalar / string results are
    returned bare.
    """
    result = data.get("result")
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, int | float | bool):
        return str(result)
    return orjson.dumps(result, option=orjson.OPT_INDENT_2).decode()


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def render_capture_status_text(data: dict[str, Any]) -> str:
    """Render capture start/stop/status payloads."""
    recording = bool(data.get("recording", False))
    entries = int(data.get("entries", 0) or 0)
    state = "recording" if recording else "stopped"
    return f"{state} | {entries} entries"


def render_capture_analyze_text(data: dict[str, Any]) -> str:
    """Render capture analyze as ``<N> patterns`` + one line per pattern."""
    patterns: list[Any] = list(data.get("patterns") or [])
    if not patterns:
        return "0 patterns"
    lines = [f"{len(patterns)} patterns"]
    for raw in patterns:
        if not isinstance(raw, dict):
            continue
        pat = _as_dict(raw)
        method = str(pat.get("method", "") or "")
        path = str(pat.get("path", "") or "")
        domain = str(pat.get("domain", "") or "")
        count = int(pat.get("call_count", 0) or 0)
        strategy = str(pat.get("strategy", "") or "")
        lines.append(f"  {method:6s} {domain}{path} | {count}x | strategy={strategy}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def render_cookies_export_text(data: dict[str, Any]) -> str:
    """Render cookies export as ``domain | name=value`` per line.

    The domain column matters when a multi-domain export is unfiltered —
    without it an agent piping ``cookies export | grep session`` can't tell
    which site the cookie belongs to, and is unaware of leakage from any
    third party that happens to share the cookie name. Agents should still
    prefer ``cloak cookies export --url <site>`` to scope the export, but
    a domain-labeled line keeps the unfiltered output honest.
    """
    cookies: list[Any] = list(data.get("cookies") or [])
    if not cookies:
        return "no cookies"
    lines: list[str] = []
    for raw in cookies:
        if not isinstance(raw, dict):
            continue
        cookie = _as_dict(raw)
        domain = str(cookie.get("domain", "") or "")
        name = str(cookie.get("name", "") or "")
        val = str(cookie.get("value", "") or "")
        # Domain is usually present (daemon enriches it from Playwright/CDP)
        # but raw RemoteBridge replies can omit it. Print ``? | name=value``
        # in that case so the column count stays consistent for pipelines —
        # a missing domain becomes loud, not silently shifted into another
        # column.
        domain_col = domain if domain else "?"
        lines.append(f"{domain_col} | {name}={val}")
    return "\n".join(lines)


def render_cookies_import_text(data: dict[str, Any]) -> str:
    """Render cookies import as ``imported <N> cookies``."""
    return f"imported {int(data.get('imported', 0) or 0)} cookies"


def render_cdp_endpoint_text(data: dict[str, Any]) -> str:
    """Render CDP endpoint as the bare ws_endpoint URL (pipe-friendly)."""
    return str(data.get("ws_endpoint", "") or "")


def render_dialog_status_text(data: dict[str, Any]) -> str:
    if not data.get("pending"):
        return "no pending dialog"
    raw_dlg: Any = data.get("dialog") or {}
    if not isinstance(raw_dlg, dict):
        return "pending dialog"
    dlg = _as_dict(raw_dlg)
    dtype = str(dlg.get("type", "") or "dialog")
    msg = str(dlg.get("message", "") or "")
    return f"pending: {dtype} {msg!r}" if msg else f"pending: {dtype}"


def render_dialog_handle_text(data: dict[str, Any]) -> str:
    action = str(data.get("action", "") or "handled")
    if action == "accept":
        return "accepted"
    if action in ("dismiss", "cancel"):
        return "dismissed"
    return action


def render_wait_text(data: dict[str, Any]) -> str:
    """Render wait results — ``matched <thing> | <ms>ms`` on success."""
    condition = str(data.get("condition", "") or "")
    target = str(data.get("value", "") or "")
    elapsed = data.get("elapsed_ms")
    suffix = f" | {int(elapsed)}ms" if isinstance(elapsed, int | float) else ""
    if condition and target:
        return f"matched {condition}={target}{suffix}"
    if condition:
        return f"matched {condition}{suffix}"
    return f"matched{suffix}"


def render_upload_text(data: dict[str, Any]) -> str:
    """Render upload result — ``uploaded N files to [index]``."""
    count = int(data.get("uploaded", data.get("count", 0)) or 0)
    index = data.get("index")
    base = f"uploaded {count} file{'s' if count != 1 else ''}"
    if index is not None:
        base = f"{base} to [{index}]"
    return base


def render_frame_list_text(data: dict[str, Any]) -> str:
    """Render frame list — git-branch style with `*` on current frame."""
    frames: list[Any] = list(data.get("frames") or [])
    if not frames:
        return "no frames"
    lines: list[str] = []
    for raw in frames:
        if not isinstance(raw, dict):
            continue
        frame = _as_dict(raw)
        marker = "*" if frame.get("is_current") else " "
        name = str(frame.get("name", "") or "(unnamed)")
        url = str(frame.get("url", "") or "")
        lines.append(f"{marker} {name} | {url}")
    return "\n".join(lines)


def render_frame_focus_text(data: dict[str, Any]) -> str:
    """Render frame focus — ``focused frame <name>`` or ``focused main frame``."""
    if data.get("main"):
        return "focused main frame"
    name = data.get("name") or data.get("frame_name")
    if name:
        return f"focused frame {name!r}"
    url = data.get("url")
    if url:
        return f"focused frame at {url}"
    return "focused frame"


def render_profile_list_text(data: dict[str, Any]) -> str:
    """Render profile list — bare names, one per line."""
    profiles: list[Any] = list(data.get("profiles") or [])
    if not profiles:
        return "no profiles"
    lines: list[str] = []
    for raw in profiles:
        if isinstance(raw, str):
            lines.append(raw)
        elif isinstance(raw, dict):
            entry = _as_dict(raw)
            lines.append(str(entry.get("name", "") or ""))
    return "\n".join(lines)


def render_resume_text(data: dict[str, Any]) -> str:
    """Render the resume snapshot as a key:value block.

    The resume writer payload is open-ended so we render the canonical
    fields agents care about (url, title, tabs, last action) and skip the
    rest. JSON mode still gets the full payload.
    """
    lines: list[str] = []
    url = str(data.get("url", "") or "")
    title = _clip_title(str(data.get("title", "") or ""))
    if url:
        lines.append(f"url: {url}")
    if title:
        lines.append(f"title: {title}")
    tier = data.get("stealth_tier")
    if tier:
        lines.append(f"tier: {tier}")
    if data.get("capture_active"):
        lines.append("capture: recording")
    tabs_raw: list[Any] = list(data.get("tabs") or [])
    if tabs_raw:
        lines.append(f"tabs: {len(tabs_raw)}")
        for raw_tab in tabs_raw[:5]:
            if not isinstance(raw_tab, dict):
                continue
            tab = _as_dict(raw_tab)
            tab_id = tab.get("tab_id", "?")
            tab_url = str(tab.get("url", "") or "")
            lines.append(f"  {tab_id}: {tab_url}")
    last = data.get("last_action") or data.get("action_summary")
    if isinstance(last, dict):
        last_d = _as_dict(last)
        kind = str(last_d.get("kind", "") or "")
        target = str(last_d.get("target", "") or last_d.get("url", "") or "")
        if kind:
            lines.append(f"last_action: {kind} {target}".rstrip())
    return "\n".join(lines) if lines else "(no resume state)"


def render_fetch_text(data: dict[str, Any]) -> str:
    """Render an HTTP fetch result.

    The body is the most useful payload for agents (often JSON they want to
    pipe to ``jq``), so render it bare. Status / content-type metadata goes
    to a header line *only* when there's no body — otherwise piping into a
    parser would choke on a multi-section response.
    """
    body = data.get("body")
    if isinstance(body, str) and body:
        return body
    status = data.get("status")
    ctype = data.get("content_type", "")
    return f"status={status} content_type={ctype}"


# ---------------------------------------------------------------------------
# One-liners previously inlined into route handlers
# ---------------------------------------------------------------------------


def render_shutdown_text(_data: dict[str, Any]) -> str:
    """Render ``POST /shutdown`` — fixed ``stopped`` token."""
    return "stopped"


def render_batch_text(data: dict[str, Any]) -> str:
    """Render ``POST /action/batch`` as ``batch: N/M completed`` plus optional abort."""
    completed = int(data.get("completed", 0) or 0)
    total = int(data.get("total", 0) or 0)
    line = f"batch: {completed}/{total} completed"
    if data.get("aborted_reason"):
        line += f" | aborted: {data['aborted_reason']}"
    return line


def render_capture_export_text(data: dict[str, Any]) -> str:
    """Render ``GET /capture/export`` — pretty-print the HAR/JSON body.

    The export endpoint returns structured data; emitting it as indented JSON
    keeps the existing pipe-to-file UX (``cloak capture export > out.har``).
    """
    return orjson.dumps(data, option=orjson.OPT_INDENT_2).decode()


def render_capture_clear_text(data: dict[str, Any]) -> str:
    """Render ``POST /capture/clear`` — ``cleared N entries``."""
    n = int(data.get("entries", 0) or 0)
    return f"cleared {n} entries"


def render_profile_create_text(data: dict[str, Any]) -> str:
    """Render ``POST /profile/create`` — ``created profile "name"``.

    The daemon returns ``{"created": "<name>"}``; we read the same field so
    the text path stays in sync with the persisted profile name (rather than
    echoing the user's request before name validation may have suffixed it).
    """
    name = str(data.get("created", "") or "")
    return f'created profile "{name}"'


def render_profile_delete_text(data: dict[str, Any]) -> str:
    """Render ``POST /profile/delete`` — ``deleted profile "name"``."""
    name = str(data.get("deleted", "") or "")
    return f'deleted profile "{name}"'


def render_profile_create_from_current_text(data: dict[str, Any]) -> str:
    """Render ``POST /profile/create-from-current`` with cookie count.

    ``created profile "<name>" (<N> cookies)`` — name is whatever the
    service persisted (suffix-renamed when ``--name`` collided).
    """
    name = str(data.get("profile", "") or "")
    cookies = int(data.get("cookie_count", 0) or 0)
    return f'created profile "{name}" ({cookies} cookies)'


def render_bridge_claim_text(data: dict[str, Any]) -> str:
    """Render ``POST /bridge/claim`` — ``claimed [tab_id] url``.

    The daemon route always wraps the response in ``_ok(...)`` so the inner
    payload is a dict — but Chrome Extension older builds sometimes return
    a bare string under ``data``; in that case ``data.get`` returns ``None``
    and we fall back to a ``claimed`` placeholder rather than crashing.
    """
    tab_id: Any = data.get("tab_id") or data.get("tabId") or "?"
    url: Any = data.get("url", "")
    return f"claimed [{tab_id}] {url}".rstrip()


def render_bridge_finalize_text(data: dict[str, Any], *, mode: str) -> str:
    """Render ``POST /bridge/finalize`` — ``<mode> <N> tabs``.

    ``mode`` is the caller-supplied finalize mode (close / handoff / deliverable);
    the count comes from the response (``count`` preferred, ``tabs`` fallback).
    """
    count_val: Any = data.get("count", data.get("tabs", 0))
    count = int(count_val or 0)
    return f"{mode} {count} tabs"


def render_token_text(data: dict[str, Any]) -> str:
    """Render ``POST /bridge/token/reset`` — bare token string for pipe usage."""
    return str(data.get("token", "") or "")
