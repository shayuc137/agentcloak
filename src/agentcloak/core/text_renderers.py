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
    "render_breakpoint_list_text",
    "render_breakpoint_set_text",
    "render_bridge_claim_text",
    "render_bridge_finalize_text",
    "render_capture_analyze_text",
    "render_capture_clear_text",
    "render_capture_export_text",
    "render_capture_status_text",
    "render_cdp_endpoint_text",
    "render_clipboard_read_text",
    "render_clipboard_write_text",
    "render_console_clear_text",
    "render_console_text",
    "render_cookie_delete_text",
    "render_cookie_set_text",
    "render_cookies_clear_text",
    "render_cookies_export_text",
    "render_cookies_import_text",
    "render_coverage_get_text",
    "render_cpu_profile_text",
    "render_debugger_evaluate_text",
    "render_debugger_op_text",
    "render_debugger_search_text",
    "render_debugger_state_text",
    "render_dialog_handle_text",
    "render_dialog_status_text",
    "render_doctor_detail_text",
    "render_doctor_text",
    "render_download_list_text",
    "render_download_text",
    "render_evaluate_text",
    "render_fetch_text",
    "render_frame_focus_text",
    "render_frame_list_text",
    "render_graphql_text",
    "render_headers_text",
    "render_health_text",
    "render_heap_snapshot_text",
    "render_launch_text",
    "render_navigate_text",
    "render_network_text",
    "render_paused_info_text",
    "render_pdf_text",
    "render_performance_metrics_text",
    "render_profile_create_from_current_text",
    "render_profile_create_text",
    "render_profile_delete_text",
    "render_profile_list_text",
    "render_profiler_op_text",
    "render_resume_text",
    "render_route_list_text",
    "render_route_op_text",
    "render_scope_variables_text",
    "render_screenshot_text",
    "render_script_add_text",
    "render_script_list_text",
    "render_script_remove_text",
    "render_script_source_text",
    "render_scripts_list_text",
    "render_serve_status_text",
    "render_serve_stop_text",
    "render_session_list_text",
    "render_shutdown_text",
    "render_snapshot_text",
    "render_sourcemap_get_text",
    "render_sourcemap_list_text",
    "render_sourcemap_lookup_text",
    "render_sourcemap_source_content_text",
    "render_sourcemap_sources_text",
    "render_spell_list_text",
    "render_spell_run_text",
    "render_sse_messages_text",
    "render_storage_text",
    "render_tab_list_text",
    "render_tab_op_text",
    "render_token_text",
    "render_upload_text",
    "render_wait_text",
    "render_ws_list_text",
    "render_ws_messages_text",
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
    version = str(data.get("version", "") or "")
    tier = str(data.get("stealth_tier", data.get("active_tier", "?")) or "?")
    browser_ready = data.get("browser_ready")
    seq = int(data.get("seq", 0) or 0)
    route_count = int(data.get("route_count", 0) or 0)
    status = "ready" if browser_ready else "not-ready"
    parts: list[str] = []
    if version:
        parts.append(f"v{version}")
    parts.extend([f"tier: {tier}", f"browser: {status}", f"seq: {seq}"])
    if route_count:
        parts.append(f"routes: {route_count}")
    current_url = data.get("current_url")
    if current_url:
        parts.append(f"url: {current_url}")
    # ``page_valid`` defaults to True; surface only the False case so a
    # healthy daemon's one-liner stays uncluttered. Agents that see
    # ``page: invalid`` know to navigate again before screenshot/evaluate.
    if browser_ready and data.get("page_valid") is False:
        parts.append("page: invalid")
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
# Session
# ---------------------------------------------------------------------------


def render_session_list_text(data: dict[str, Any]) -> str:
    """Render ``/session/list`` or ``/session/close``."""
    if "closed" in data:
        sid = str(data.get("session_id", ""))
        return f"closed: {data['closed']} | session: {sid}"
    sessions: list[Any] = list(data.get("sessions") or [])
    if not sessions:
        return "no named sessions"
    lines: list[str] = []
    for raw in sessions:
        if not isinstance(raw, dict):
            continue
        s = _as_dict(raw)
        sid = str(s.get("session_id", ""))
        state = str(s.get("state", ""))
        tier = str(s.get("tier", ""))
        idle = s.get("idle_seconds", 0)
        lines.append(f"{sid} | {state} | {tier} | idle {idle}s")
    return "\n".join(lines) if lines else "no named sessions"


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
    # ``page_valid`` is True by default — only flag the False case so the
    # happy-path output stays terse. When the last navigate failed agents
    # need to see this immediately, hence the explicit "INVALID" rather
    # than a quiet boolean.
    if "page_valid" in data and data.get("page_valid") is False:
        lines.append("page: INVALID (last navigate failed)")
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


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------


def _format_doctor_status_line(runtime: dict[str, Any]) -> str:
    """Build the doctor runtime status line.

    Returns either a single string ``daemon not running ...`` when the runtime
    block isn't available, or the pipe-separated environment summary expected
    by the doctor PRD (browser | display | humanize | proxy | profile).

    The block lives in ``runtime`` so the caller can pass either a real
    ``/health`` dict or ``{}`` for "daemon not running" rendering.
    """
    daemon_ok = bool(runtime.get("daemon_ok", False))
    if not daemon_ok:
        return "daemon not running (auto-starts on first command)"

    parts: list[str] = []
    browser_desc = str(runtime.get("browser_description") or "")
    if browser_desc:
        parts.append(browser_desc)
    parts.append("headless" if runtime.get("headless", True) else "headed")
    if runtime.get("humanize"):
        parts.append("humanize")
    proxy = str(runtime.get("proxy") or "")
    parts.append(proxy if proxy else "no proxy")
    profile = str(runtime.get("active_profile") or "")
    parts.append(f"profile: {profile}" if profile else "no profile (ephemeral)")
    return " | ".join(parts)


def render_doctor_text(data: dict[str, Any]) -> str:
    """Render ``/doctor`` payload in the agent-first concise format.

    Two-line summary when everything passes:

        all N checks passed | agentcloak X.Y.Z
        CloakBrowser X.Y.Z | headless | humanize | no proxy | no profile (ephemeral)

    Failure mode lists only the failed checks plus the runtime status:

        M/N checks passed
        [fail] name | detail | hint
        [fail] name | detail | hint
        CloakBrowser ... | ...

    The ``data`` dict is expected to carry the standard doctor envelope keys
    (``checks``, ``healthy``) plus an optional ``runtime`` block populated by
    the CLI/MCP caller from the daemon ``/health`` probe. When the runtime
    block is missing or ``daemon_ok`` is False the second line falls back to
    "daemon not running (auto-starts on first command)" so the user still
    gets the agentcloak version on line 1 without a misleading environment
    summary built from defaults.
    """
    from agentcloak import __version__ as ac_version

    checks_raw: list[Any] = list(data.get("checks") or [])
    checks: list[dict[str, Any]] = [
        _as_dict(c) for c in checks_raw if isinstance(c, dict)
    ]
    failed = [c for c in checks if not c.get("ok")]
    total = len(checks)
    passed = total - len(failed)

    # The runtime block is opt-in metadata the caller (CLI/MCP) layers on top
    # of the DiagnosticService report. We render whatever it contains; the
    # service itself stays pure.
    raw_runtime = data.get("runtime")
    runtime = _as_dict(raw_runtime) if isinstance(raw_runtime, dict) else {}
    status_line = _format_doctor_status_line(runtime)

    version_warn = ""
    if runtime.get("version_mismatch"):
        daemon_ver = str(runtime.get("daemon_version", "?"))
        local_ver = str(runtime.get("local_version", ac_version))
        version_warn = (
            f"[warn] daemon version {daemon_ver} != local {local_ver}"
            " — restart: cloak daemon stop && cloak daemon start -b"
        )

    lines: list[str] = []
    if not failed:
        lines.append(f"all {total} checks passed | agentcloak {ac_version}")
        if version_warn:
            lines.append(version_warn)
        lines.append(status_line)
        return "\n".join(lines)

    lines.append(f"{passed}/{total} checks passed")
    for check in failed:
        name = str(check.get("name", "") or "")
        detail = str(check.get("detail", "") or "")
        hint = str(check.get("hint", "") or "")
        line = f"[fail] {name} | {detail}"
        if hint:
            line += f" | {hint}"
        lines.append(line)
    if version_warn:
        lines.append(version_warn)
    lines.append(status_line)
    return "\n".join(lines)


def render_doctor_detail_text(data: dict[str, Any]) -> str:
    """Render the full per-check ``--detail`` view (backward-compatible).

    Mirrors the pre-v0.3.x layout — one ``[ok] / [info] / [fail]`` line per
    check, derived from ``check["level"]`` or ``ok`` when ``level`` is absent.
    Failure / info lines append the hint inline so the user can act on it.
    Doctor's ``--detail`` mode is the safety net for advanced debugging; new
    code should default to :func:`render_doctor_text`.
    """
    checks_raw: list[Any] = list(data.get("checks") or [])
    lines: list[str] = []
    for raw in checks_raw:
        if not isinstance(raw, dict):
            continue
        check = _as_dict(raw)
        ok = bool(check.get("ok"))
        level = str(check.get("level") or ("ok" if ok else "fail"))
        name = str(check.get("name", "") or "")
        detail = str(check.get("detail", "") or "")
        line = f"[{level}] {name} | {detail}"
        # Same display rule as the legacy renderer: only suffix the hint for
        # non-ok rows so the happy path stays clean.
        hint = str(check.get("hint", "") or "")
        if level != "ok" and hint:
            line += f" | hint: {hint}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Console (7a R1)
# ---------------------------------------------------------------------------


def render_console_text(data: dict[str, Any]) -> str:
    """Render ``/console`` as ``[level] text (url:line)`` per message.

    Errors get an ``!`` prefix so an agent scanning the output can spot
    uncaught exceptions among ordinary logs. The trailing ``seq=N`` lets the
    agent pass ``--since N`` next time to page only new messages.
    """
    entries: list[Any] = list(data.get("entries") or [])
    if not entries:
        return "no console messages"
    lines: list[str] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        entry = _as_dict(raw)
        level = str(entry.get("level", "") or "log")
        text = str(entry.get("text", "") or "")
        marker = "!" if entry.get("is_error") else " "
        url = str(entry.get("url", "") or "")
        line_no = entry.get("line")
        loc = ""
        if url:
            loc = f" ({url}:{line_no})" if line_no is not None else f" ({url})"
        lines.append(f"{marker}[{level}] {text}{loc}")
    seq = int(data.get("seq", 0) or 0)
    lines.append(f"--- seq={seq} ---")
    return "\n".join(lines)


def render_console_clear_text(_data: dict[str, Any]) -> str:
    """Render ``POST /console/clear`` — fixed confirmation."""
    return "console cleared"


# ---------------------------------------------------------------------------
# Download (7a R2)
# ---------------------------------------------------------------------------


def render_download_text(data: dict[str, Any]) -> str:
    """Render a single download (url/wait) as ``saved <path> (<size> bytes)``."""
    path = str(data.get("path", "") or "")
    size = int(data.get("size", 0) or 0)
    if not path:
        return "no download"
    return f"saved {path} ({size} bytes)"


def render_download_list_text(data: dict[str, Any]) -> str:
    """Render ``/download/list`` as one ``<path> | <size> bytes`` per line."""
    downloads: list[Any] = list(data.get("downloads") or [])
    if not downloads:
        return "no downloads"
    lines: list[str] = []
    for raw in downloads:
        if not isinstance(raw, dict):
            continue
        dl = _as_dict(raw)
        path = str(dl.get("path", "") or "")
        size = int(dl.get("size", 0) or 0)
        lines.append(f"{path} | {size} bytes")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Storage (7a R4)
# ---------------------------------------------------------------------------


def render_storage_text(data: dict[str, Any]) -> str:
    """Render a storage op result.

    A single-key get prints the bare value (pipe-friendly); a full dump prints
    ``key=value`` per line. Mutating ops (set/delete/clear) print a short
    confirmation that names the area and key.
    """
    area = str(data.get("type", "") or "local")
    if data.get("set"):
        return f"set {area}.{data.get('key', '')}"
    if data.get("deleted"):
        return f"deleted {area}.{data.get('key', '')}"
    if data.get("cleared"):
        return f"cleared {area}Storage"
    # Read path: ``value`` is a string (single key), an object (full dump),
    # or null/None (missing key).
    value = data.get("value")
    if value is None:
        key = data.get("key", "")
        return f"{area}.{key} not set" if key else f"{area}Storage is empty"
    if isinstance(value, dict):
        items = _as_dict(value)
        if not items:
            return f"{area}Storage is empty"
        return "\n".join(f"{k}={v}" for k, v in items.items())
    return str(value)


# ---------------------------------------------------------------------------
# Clipboard (7a R5)
# ---------------------------------------------------------------------------


def render_clipboard_read_text(data: dict[str, Any]) -> str:
    """Render ``/clipboard/read`` — the bare clipboard text (pipe-friendly)."""
    return str(data.get("text", "") or "")


def render_clipboard_write_text(data: dict[str, Any]) -> str:
    """Render ``/clipboard/write`` — ``wrote N chars to clipboard``."""
    length = int(data.get("length", 0) or 0)
    return f"wrote {length} chars to clipboard"


# ---------------------------------------------------------------------------
# PDF (7a R6)
# ---------------------------------------------------------------------------


def render_pdf_text(data: dict[str, Any]) -> str:
    """Render ``/pdf`` — saved path when the daemon wrote the file, else a summary.

    The CLI ``pdf`` command decodes base64 and writes the file locally, then
    renders its own path; this renderer covers the daemon-side ``output_path``
    case and API/MCP callers.
    """
    path = str(data.get("path", "") or "")
    size = int(data.get("size", 0) or 0)
    if path:
        return f"saved {path} ({size} bytes)"
    return f"pdf rendered | {size} bytes"


# ---------------------------------------------------------------------------
# Serve (7a R7)
# ---------------------------------------------------------------------------


def render_serve_status_text(data: dict[str, Any]) -> str:
    """Render serve start/status — ``serving <dir> at <url>`` or ``not running``."""
    if not data.get("running"):
        return "file server not running"
    directory = str(data.get("directory", "") or "")
    url = str(data.get("url", "") or "")
    return f"serving {directory} at {url}"


def render_serve_stop_text(data: dict[str, Any]) -> str:
    """Render ``/serve/stop`` — confirmation or no-op note."""
    return "file server stopped" if data.get("stopped") else "no file server running"


# ---------------------------------------------------------------------------
# Cookies CRUD (7a R3)
# ---------------------------------------------------------------------------


def render_cookie_set_text(data: dict[str, Any]) -> str:
    """Render ``/cookies/set`` — ``set N cookies``."""
    return f"set {int(data.get('set', 0) or 0)} cookies"


def render_cookies_clear_text(_data: dict[str, Any]) -> str:
    """Render ``/cookies/clear`` — fixed confirmation."""
    return "cleared all cookies"


def render_cookie_delete_text(data: dict[str, Any]) -> str:
    """Render ``/cookies/delete`` — ``deleted N cookie(s) named "name"``."""
    n = int(data.get("deleted", 0) or 0)
    name = str(data.get("name", "") or "")
    return f'deleted {n} cookie{"s" if n != 1 else ""} named "{name}"'


# ---------------------------------------------------------------------------
# Init scripts (7b T1.1)
# ---------------------------------------------------------------------------


def render_script_add_text(data: dict[str, Any]) -> str:
    """Render ``/script/add`` — the identifier (pipe-friendly for removal)."""
    identifier = str(data.get("identifier", "") or "")
    preset = data.get("preset")
    if not identifier:
        return "no identifier"
    return f"{identifier} ({preset})" if preset else identifier


def render_script_remove_text(data: dict[str, Any]) -> str:
    """Render ``/script/remove`` — confirmation or not-found note."""
    return "removed" if data.get("removed") else "no such script"


def render_script_list_text(data: dict[str, Any]) -> str:
    """Render ``/script/list`` — one ``<identifier>: <source preview>`` per line."""
    scripts: list[Any] = list(data.get("scripts") or [])
    if not scripts:
        return "no init scripts"
    lines: list[str] = []
    for raw in scripts:
        if not isinstance(raw, dict):
            continue
        entry = _as_dict(raw)
        ident = str(entry.get("identifier", "") or "")
        # Collapse the source to a single line so the listing stays scannable.
        src = " ".join(str(entry.get("source", "") or "").split())
        lines.append(f"{ident}: {src}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Network route interception (7b T1.3)
# ---------------------------------------------------------------------------


def render_route_op_text(data: dict[str, Any]) -> str:
    """Render ``/route/add`` and ``/route/remove`` — a short state summary."""
    count = int(data.get("count", 0) or 0)
    removed = int(data.get("removed", 0) or 0)
    if removed:
        return f"removed {removed} rule{'s' if removed != 1 else ''} ({count} active)"
    pattern = str(data.get("pattern", "") or "")
    if pattern:
        return f"added rule {pattern} ({count} active)"
    return f"{count} active rules"


def render_route_list_text(data: dict[str, Any]) -> str:
    """Render ``/route/list`` — one ``<action> <pattern> [filters]`` per line."""
    rules: list[Any] = list(data.get("rules") or [])
    if not rules:
        return "no route rules"
    lines: list[str] = []
    for raw in rules:
        if not isinstance(raw, dict):
            continue
        rule = _as_dict(raw)
        action = str(rule.get("action", "") or "")
        pattern = str(rule.get("pattern", "") or "")
        extras: list[str] = []
        for key in ("method", "resource_type", "status", "content_type"):
            val = rule.get(key)
            if val:
                extras.append(f"{key}={val}")
        suffix = f" [{', '.join(extras)}]" if extras else ""
        lines.append(f"{action} {pattern}{suffix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Header injection (7b T1.2)
# ---------------------------------------------------------------------------


def render_headers_text(data: dict[str, Any]) -> str:
    """Render ``/emulation/headers`` — ``set N headers`` or a cleared note."""
    headers = data.get("headers")
    count = int(data.get("count", 0) or 0)
    if count == 0:
        return "cleared extra headers"
    if isinstance(headers, dict):
        names = ", ".join(sorted(_as_dict(headers).keys()))
        return f"set {count} header{'s' if count != 1 else ''}: {names}"
    return f"set {count} headers"


# ---------------------------------------------------------------------------
# GraphQL (7b T1.4)
# ---------------------------------------------------------------------------


def render_graphql_text(data: dict[str, Any]) -> str:
    """Render a GraphQL response.

    Prints the compact JSON of ``data`` (or ``errors``) so the agent gets the
    structured payload directly; falls back to the raw body when the endpoint
    returned non-JSON. The HTTP status leads so failures are obvious.
    """
    status = int(data.get("status", 0) or 0)
    errors = data.get("errors")
    payload = data.get("data")
    raw = str(data.get("raw", "") or "")

    if errors:
        body = orjson.dumps(errors).decode()
        return f"status={status} errors={body}"
    if payload is not None:
        return f"status={status} {orjson.dumps(payload).decode()}"
    if raw:
        return f"status={status} {raw}"
    return f"status={status} (empty response)"


# ---------------------------------------------------------------------------
# Streaming capture: WebSocket + SSE (7b T2)
# ---------------------------------------------------------------------------


def render_ws_list_text(data: dict[str, Any]) -> str:
    """Render ``/ws/list`` — one ``<status> <url> (<request_id>)`` per line."""
    conns: list[Any] = list(data.get("connections") or [])
    if not conns:
        return "no websocket connections"
    lines: list[str] = []
    for raw in conns:
        if not isinstance(raw, dict):
            continue
        conn = _as_dict(raw)
        status = str(conn.get("status", "") or "")
        url = str(conn.get("url", "") or "")
        request_id = str(conn.get("request_id", "") or "")
        lines.append(f"{status} {url} ({request_id})")
    return "\n".join(lines)


def render_ws_messages_text(data: dict[str, Any]) -> str:
    """Render ``/ws/messages`` — one ``<seq> <dir> <payload>`` per line.

    ``→`` marks a sent frame (client→server), ``←`` a received one. Payloads are
    collapsed to a single line so the listing stays scannable; pass the trailing
    ``seq`` back as ``--since`` to page forward.
    """
    frames: list[Any] = list(data.get("frames") or [])
    if not frames:
        return "no websocket frames"
    lines: list[str] = []
    for raw in frames:
        if not isinstance(raw, dict):
            continue
        frame = _as_dict(raw)
        seq = frame.get("seq", "")
        arrow = "→" if str(frame.get("direction", "")) == "sent" else "←"
        payload = " ".join(str(frame.get("payload", "") or "").split())
        lines.append(f"{seq} {arrow} {payload}")
    return "\n".join(lines)


def render_sse_messages_text(data: dict[str, Any]) -> str:
    """Render ``/sse/messages`` — one ``<seq> [<event>] <data>`` per line.

    The event name is shown in brackets only when the server set one (default
    SSE messages have none). Pass the trailing ``seq`` back as ``--since`` to
    page forward.
    """
    events: list[Any] = list(data.get("events") or [])
    if not events:
        return "no sse events"
    lines: list[str] = []
    for raw in events:
        if not isinstance(raw, dict):
            continue
        event = _as_dict(raw)
        seq = event.get("seq", "")
        name = str(event.get("event_name", "") or "")
        payload = " ".join(str(event.get("data", "") or "").split())
        prefix = f"{seq} [{name}]" if name else f"{seq}"
        lines.append(f"{prefix} {payload}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Debugger (7b T3)
# ---------------------------------------------------------------------------


def render_debugger_state_text(data: dict[str, Any]) -> str:
    """Render the debugger state summary (enable/disable/resume/xhr/skip)."""
    enabled = "enabled" if data.get("enabled") else "disabled"
    paused = " paused" if data.get("paused") else ""
    bp = int(data.get("breakpoint_count", 0) or 0)
    xhr = int(data.get("xhr_breakpoint_count", 0) or 0)
    return f"debugger {enabled}{paused} ({bp} breakpoints, {xhr} xhr)"


def render_debugger_op_text(data: dict[str, Any]) -> str:
    """Render a remove-style op — confirmation plus the resulting state."""
    note = "removed" if data.get("removed") else "not tracked"
    return f"{note}; {render_debugger_state_text(data)}"


def render_breakpoint_set_text(data: dict[str, Any]) -> str:
    """Render ``/debugger/breakpoint/set`` — the id (pipe-friendly for removal)."""
    bp = data.get("breakpoint")
    if isinstance(bp, dict):
        info = _as_dict(bp)
        bid = str(info.get("breakpoint_id", "") or "")
        url = str(info.get("url", "") or "")
        line = info.get("line", "")
        if bid:
            return f"{bid} ({url}:{line})"
    return "no breakpoint id"


def render_breakpoint_list_text(data: dict[str, Any]) -> str:
    """Render ``/debugger/breakpoint/list`` — one breakpoint/pattern per line."""
    bps: list[Any] = list(data.get("breakpoints") or [])
    xhr: list[Any] = list(data.get("xhr_patterns") or [])
    if not bps and not xhr:
        return "no breakpoints"
    lines: list[str] = []
    for raw in bps:
        if not isinstance(raw, dict):
            continue
        bp = _as_dict(raw)
        bid = str(bp.get("breakpoint_id", "") or "")
        url = str(bp.get("url", "") or "")
        line = bp.get("line", "")
        cond = str(bp.get("condition", "") or "")
        suffix = f" if {cond}" if cond else ""
        lines.append(f"{bid} {url}:{line}{suffix}")
    for pattern in xhr:
        shown = str(pattern) if pattern else "(any)"
        lines.append(f"xhr {shown}")
    return "\n".join(lines)


def _render_call_frames(frames: list[Any]) -> list[str]:
    """Render call frames as ``#N functionName (scriptId:line) [callFrameId]``."""
    lines: list[str] = []
    for i, raw in enumerate(frames):
        if not isinstance(raw, dict):
            continue
        frame = _as_dict(raw)
        fn = str(frame.get("functionName", "") or "<anonymous>")
        loc = frame.get("location")
        loc_str = ""
        if isinstance(loc, dict):
            location = _as_dict(loc)
            loc_str = f"{location.get('scriptId', '')}:{location.get('lineNumber', '')}"
        cfid = str(frame.get("callFrameId", "") or "")
        lines.append(f"#{i} {fn} ({loc_str}) [{cfid}]")
    return lines


def render_paused_info_text(data: dict[str, Any]) -> str:
    """Render ``/debugger/paused-info`` and ``/debugger/step`` — paused snapshot.

    Shows the stop reason then the call stack, one frame per line. Each frame
    ends with its ``callFrameId`` in brackets so an agent can copy it straight
    into ``debugger evaluate``.
    """
    if not data.get("paused"):
        return "not paused"
    reason = str(data.get("reason", "") or "")
    frames: list[Any] = list(data.get("call_frames") or [])
    header = f"paused ({reason})" if reason else "paused"
    lines = [header, *_render_call_frames(frames)]
    return "\n".join(lines)


def render_scope_variables_text(data: dict[str, Any]) -> str:
    """Render ``/debugger/scope-variables`` — one ``name = value`` per line."""
    variables: list[Any] = list(data.get("variables") or [])
    if not variables:
        return "no variables"
    lines: list[str] = []
    for raw in variables:
        if not isinstance(raw, dict):
            continue
        var = _as_dict(raw)
        name = str(var.get("name", "") or "")
        value = var.get("value")
        rendered = "<unavailable>"
        if isinstance(value, dict):
            v = _as_dict(value)
            # Prefer a concrete value; fall back to description (functions,
            # objects) then the bare type.
            if "value" in v:
                rendered = str(v.get("value"))
            elif v.get("description"):
                rendered = str(v.get("description"))
            else:
                rendered = str(v.get("type", "") or "")
        lines.append(f"{name} = {rendered}")
    return "\n".join(lines)


def render_debugger_evaluate_text(data: dict[str, Any]) -> str:
    """Render ``/debugger/evaluate`` — the value, or the thrown exception."""
    exc = data.get("exception")
    if exc:
        body = orjson.dumps(exc).decode()
        return f"exception {body}"
    result = data.get("result")
    if isinstance(result, dict):
        r = _as_dict(result)
        if "value" in r:
            return str(r.get("value"))
        if r.get("description"):
            return str(r.get("description"))
        return str(r.get("type", "") or "(no value)")
    return "(no value)"


def render_scripts_list_text(data: dict[str, Any]) -> str:
    """Render ``/debugger/scripts`` — one ``<script_id> <url>[ map]`` per line."""
    scripts: list[Any] = list(data.get("scripts") or [])
    if not scripts:
        return "no scripts"
    lines: list[str] = []
    for raw in scripts:
        if not isinstance(raw, dict):
            continue
        s = _as_dict(raw)
        sid = str(s.get("script_id", "") or "")
        url = str(s.get("url", "") or "<inline>")
        has_map = " [map]" if s.get("source_map_url") else ""
        lines.append(f"{sid} {url}{has_map}")
    return "\n".join(lines)


def render_script_source_text(data: dict[str, Any]) -> str:
    """Render ``/debugger/script-source`` — the raw source text."""
    return str(data.get("source", "") or "")


def render_debugger_search_text(data: dict[str, Any]) -> str:
    """Render ``/debugger/search`` — one ``<line>: <content>`` per match."""
    matches: list[Any] = list(data.get("matches") or [])
    if not matches:
        return "no matches"
    lines: list[str] = []
    for raw in matches:
        if not isinstance(raw, dict):
            continue
        m = _as_dict(raw)
        line_no = m.get("lineNumber", "")
        content = " ".join(str(m.get("lineContent", "") or "").split())
        lines.append(f"{line_no}: {content}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SourceMap (7b T4)
# ---------------------------------------------------------------------------


def render_sourcemap_list_text(data: dict[str, Any]) -> str:
    """Render ``/sourcemap/list`` — one ``<script_id> <url>`` per mapped script."""
    maps: list[Any] = list(data.get("maps") or [])
    if not maps:
        return "no source maps"
    lines: list[str] = []
    for raw in maps:
        if not isinstance(raw, dict):
            continue
        entry = _as_dict(raw)
        sid = str(entry.get("script_id", "") or "")
        url = str(entry.get("url", "") or "<inline>")
        lines.append(f"{sid} {url}")
    return "\n".join(lines)


def render_sourcemap_get_text(data: dict[str, Any]) -> str:
    """Render ``/sourcemap/get`` — a compact metadata summary line.

    Leads with the source/mapping counts an agent uses to decide whether the
    map is worth walking, then lists the source paths (one per line) so the
    next ``lookup`` / ``source-content`` call has the paths to hand.
    """
    sources: list[Any] = list(data.get("sources") or [])
    mapping_count = int(data.get("mapping_count", 0) or 0)
    has_content = bool(data.get("has_sources_content"))
    content_note = " with-content" if has_content else ""
    header = (
        f"source map: {len(sources)} sources, {mapping_count} mappings{content_note}"
    )
    lines = [header, *(str(s) for s in sources)]
    return "\n".join(lines)


def render_sourcemap_lookup_text(data: dict[str, Any]) -> str:
    """Render ``/sourcemap/lookup`` — ``<source>:<line>:<col>[ name]`` or no match."""
    if not data.get("matched"):
        return "no mapping"
    source = str(data.get("source", "") or "")
    line = data.get("original_line", "")
    col = data.get("original_column", "")
    name = str(data.get("name", "") or "")
    suffix = f" {name}" if name else ""
    return f"{source}:{line}:{col}{suffix}"


def render_sourcemap_sources_text(data: dict[str, Any]) -> str:
    """Render ``/sourcemap/sources`` — one source path per line."""
    sources: list[Any] = list(data.get("sources") or [])
    if not sources:
        return "no sources"
    return "\n".join(str(s) for s in sources)


def render_sourcemap_source_content_text(data: dict[str, Any]) -> str:
    """Render ``/sourcemap/source-content`` — the original source text.

    Returns the embedded text verbatim (pipe-friendly: ``... > out.js``). When
    the map declares the source but ships no content for it, emit a short marker
    on stderr-style note instead of an empty body so the agent isn't left
    guessing whether the fetch failed.
    """
    if not data.get("available"):
        return "no embedded source content"
    return str(data.get("content", "") or "")


# ---------------------------------------------------------------------------
# Profiler: coverage / CPU / heap (7f)
# ---------------------------------------------------------------------------


def render_profiler_op_text(data: dict[str, Any]) -> str:
    """Render a profiler lifecycle op (coverage/cpu start/stop) acknowledgement."""
    if data.get("started"):
        return "profiler started"
    if data.get("stopped"):
        return "profiler stopped"
    return "ok"


def render_coverage_get_text(data: dict[str, Any]) -> str:
    """Render a precise-coverage summary, most-covered script first.

    Header counts the totals; each line is ``<url>: <covered>/<total> functions
    covered (<pct>%)``. The fully-covered scripts at the top are the code paths
    the triggered action actually exercised — usually where the crypto lives.
    """
    scripts: list[Any] = list(data.get("scripts") or [])
    script_count = int(data.get("script_count", 0) or 0)
    funcs_total = int(data.get("functions_total", 0) or 0)
    if not scripts:
        return "no coverage data (start coverage, trigger an action, then get)"

    header = (
        f"Coverage: {script_count} script{'s' if script_count != 1 else ''}, "
        f"{funcs_total} function{'s' if funcs_total != 1 else ''}"
    )
    lines: list[str] = [header]
    for raw in scripts:
        if not isinstance(raw, dict):
            continue
        s = _as_dict(raw)
        url = str(s.get("url", "") or "") or f"<script {s.get('script_id', '')}>"
        covered = int(s.get("functions_covered", 0) or 0)
        total = int(s.get("functions_total", 0) or 0)
        pct = float(s.get("coverage_pct", 0.0) or 0.0)
        lines.append(f"  {url}: {covered}/{total} functions covered ({pct}%)")
    return "\n".join(lines)


def render_cpu_profile_text(data: dict[str, Any]) -> str:
    """Render a CPU-profile summary — the hottest functions by self time.

    ``<hits> <name> (<url>:<line>)`` per line, most CPU-hungry first. A high
    sampler hit-count concentrated in one function is the classic signature of
    crypto/signing work. The header notes the sample count and duration; the
    saved file path (if any) closes the listing.
    """
    top: list[Any] = list(data.get("top_functions") or [])
    samples = int(data.get("sample_count", 0) or 0)
    duration_us = int(data.get("duration_us", 0) or 0)
    path = str(data.get("path", "") or "")

    header = f"CPU profile: {samples} samples over {duration_us / 1000:.0f}ms"
    if not top:
        tail = f"\nsaved to {path}" if path else ""
        return f"{header} (no function samples){tail}"

    lines: list[str] = [header]
    for raw in top:
        if not isinstance(raw, dict):
            continue
        fn = _as_dict(raw)
        name = str(fn.get("name", "") or "(anonymous)")
        url = str(fn.get("url", "") or "")
        line = int(fn.get("line", 0) or 0)
        hits = int(fn.get("hits", 0) or 0)
        loc = f" ({url}:{line})" if url else ""
        lines.append(f"  {hits} {name}{loc}")
    if path:
        lines.append(f"saved to {path}")
    return "\n".join(lines)


def render_heap_snapshot_text(data: dict[str, Any]) -> str:
    """Render ``/profiler/heap/snapshot`` — the saved file path (pipe-friendly)."""
    path = str(data.get("path", "") or "")
    size = int(data.get("size", 0) or 0)
    if not path:
        return "heap snapshot failed (no data)"
    return f"{path} ({size} bytes)"


def render_performance_metrics_text(data: dict[str, Any]) -> str:
    """Render ``/performance/metrics`` — one ``<name> = <value>`` per line."""
    metrics: list[Any] = list(data.get("metrics") or [])
    if not metrics:
        return "no metrics"
    lines: list[str] = []
    for raw in metrics:
        if not isinstance(raw, dict):
            continue
        m = _as_dict(raw)
        name = str(m.get("name", "") or "")
        value = m.get("value", 0)
        lines.append(f"{name} = {value}")
    return "\n".join(lines)
