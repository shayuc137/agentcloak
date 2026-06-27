"""Shared snapshot builder — builds accessible/compact snapshots from CDP AX tree nodes.

Both PlaywrightContext and RemoteBridgeContext call build_snapshot() with raw CDP nodes.

Architecture: three-phase pipeline.
  Phase 1 — Build Tree: _visit() constructs a SnapshotNode tree from raw CDP nodes.
  Phase 2 — Dedup Passes: tree mutations that eliminate redundant content.
  Phase 3 — Flatten + Render: DFS produces cached_lines, then text output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agentcloak.browser.state import (
    CONTEXT_ROLES as _CONTEXT_ROLES,
)
from agentcloak.browser.state import (
    INTERACTIVE_ROLES as _INTERACTIVE_ROLES,
)
from agentcloak.browser.state import (
    ElementRef,
    PageSnapshot,
)

__all__ = [
    "DiffCounts",
    "FrameData",
    "SnapshotNode",
    "SnapshotResult",
    "build_snapshot",
    "count_diff",
    "diff_snapshots",
    "render_diff_tree",
    "truncate_diff_lines",
]

_INDENT_STEP = " "

_SKIP_ROLES = frozenset({"none", "InlineTextBox", "LineBreak"})

_INVISIBLE_RE = re.compile("[​‌‍⁠﻿]")

_BOOL_PROPS = frozenset(
    {
        "checked",
        "disabled",
        "expanded",
        "selected",
        "pressed",
        "invalid",
        "required",
        "focused",
        "hidden",
    }
)

_VALUE_PROPS = frozenset(
    {
        "valuemin",
        "valuemax",
        "valuenow",
        "valuetext",
        "level",
        "haspopup",
        "autocomplete",
        "url",
    }
)

_FALSE_MEANINGFUL = frozenset({"expanded"})

_TRISTATE_PROPS = frozenset({"checked", "pressed"})


@dataclass
class FrameData:
    """AX tree nodes from a child frame, with metadata for merging."""

    frame_id: str
    name: str
    url: str
    nodes: list[dict[str, Any]]


@dataclass
class SnapshotNode:
    """Intermediate tree node for the three-phase pipeline."""

    role: str
    name: str
    ref: int | None = None
    attrs: dict[str, str] = field(default_factory=lambda: {})
    children: list[SnapshotNode] = field(default_factory=lambda: [])
    backend_dom_id: int | None = None
    is_interactive: bool = False
    is_context: bool = False
    pruned: bool = False


@dataclass
class SnapshotResult:
    snapshot: PageSnapshot
    selector_map: dict[int, ElementRef]
    backend_node_map: dict[int, int]
    cached_lines: list[tuple[int, str, int | None]]


@dataclass
class DiffCounts:
    """Counts of added/changed/removed lines from a :func:`diff_snapshots` call."""

    added: int = 0
    changed: int = 0
    removed: int = 0

    @property
    def is_empty(self) -> bool:
        return self.added == 0 and self.changed == 0 and self.removed == 0


# ---------------------------------------------------------------------------
# Helper functions (unchanged)
# ---------------------------------------------------------------------------


def _clean_text(text: str) -> str:
    text = _INVISIBLE_RE.sub("", text)
    text = text.replace("\xa0", " ")
    return text.strip()


def _ax_value(obj: dict[str, Any] | None) -> Any:
    if isinstance(obj, dict):
        return obj.get("value")
    return obj


def _extract_props(node: dict[str, Any]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    props: list[dict[str, Any]] = node.get("properties", [])
    is_password = False
    for prop in props:
        pname: str = prop.get("name", "")
        val: object = _ax_value(prop.get("value"))

        if pname == "autocomplete" and isinstance(val, str) and "password" in val:
            is_password = True

        if pname in _BOOL_PROPS:
            if pname in _TRISTATE_PROPS:
                normalised = val
                if isinstance(val, bool):
                    normalised = "true" if val else "false"
                if normalised == "true":
                    attrs[pname] = ""
                elif normalised == "mixed":
                    attrs[pname] = "mixed"
            else:
                if val is True:
                    attrs[pname] = ""
                elif val is False and pname in _FALSE_MEANINGFUL:
                    attrs[pname] = "false"
        elif pname in _VALUE_PROPS and val is not None:
            attrs[pname] = str(val)

    val_raw: object = _ax_value(node.get("value"))
    if val_raw is not None and str(val_raw).strip():
        if is_password:
            attrs["value"] = "••••"
        else:
            attrs["value"] = _clean_text(str(val_raw))

    desc_raw: object = _ax_value(node.get("description"))
    if desc_raw is not None and str(desc_raw).strip():
        attrs["description"] = _clean_text(str(desc_raw))

    return attrs


def _format_attrs(attrs: dict[str, str]) -> str:
    parts: list[str] = []
    if "value" in attrs:
        parts.append(f'value="{attrs["value"]}"')
    for key in (
        "checked",
        "disabled",
        "expanded",
        "selected",
        "pressed",
        "invalid",
        "required",
        "focused",
        "hidden",
    ):
        if key in attrs:
            val = attrs[key]
            if val == "":
                parts.append(key)
            else:
                parts.append(f"{key}={val}")
    for key in (
        "level",
        "haspopup",
        "valuemin",
        "valuemax",
        "valuenow",
        "valuetext",
        "description",
    ):
        if key in attrs:
            parts.append(f"{key}={attrs[key]}")
    if "url" in attrs:
        parts.append(f'href="{attrs["url"]}"')
    return " ".join(parts)


def _should_fold(node: dict[str, Any], role: str, name: str) -> bool:
    if role in ("generic", "group", "none", ""):
        child_ids = node.get("childIds", [])
        if not name and len(child_ids) <= 1:
            return True
    return False


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _extract_focus_subtree(
    lines: list[tuple[int, str, int | None]],
    target_ref: int,
) -> list[tuple[int, str, int | None]]:
    target_idx = -1
    for i, (_, _, ref) in enumerate(lines):
        if ref == target_ref:
            target_idx = i
            break
    if target_idx < 0:
        return lines

    target_depth = lines[target_idx][0]

    ancestors: list[int] = []
    search_depth = target_depth
    for i in range(target_idx - 1, -1, -1):
        if lines[i][0] < search_depth:
            ancestors.append(i)
            search_depth = lines[i][0]
            if search_depth == 0:
                break
    ancestors.reverse()

    subtree: list[int] = [target_idx]
    for i in range(target_idx + 1, len(lines)):
        if lines[i][0] > target_depth:
            subtree.append(i)
        else:
            break

    result_indices = ancestors + subtree
    return [lines[i] for i in result_indices]


# ---------------------------------------------------------------------------
# Phase 1: Build Tree
# ---------------------------------------------------------------------------


def _build_tree(
    node_by_id: dict[str, dict[str, Any]],
    root_ids: list[str],
    *,
    selector_map: dict[int, ElementRef],
    backend_node_map: dict[int, int],
    counter: list[int],
) -> SnapshotNode:
    def visit(node_id: str) -> list[SnapshotNode]:
        node = node_by_id.get(node_id)
        if node is None:
            return []

        ignored = node.get("ignored", False)
        role = node.get("role", {}).get("value", "")
        name = _clean_text(node.get("name", {}).get("value", ""))
        child_ids: list[str] = node.get("childIds", [])

        if role in _SKIP_ROLES or ignored or _should_fold(node, role, name):
            promoted: list[SnapshotNode] = []
            for cid in child_ids:
                promoted.extend(visit(cid))
            return promoted

        role_lower = role.lower()
        is_interactive = role_lower in _INTERACTIVE_ROLES
        is_context = role_lower in _CONTEXT_ROLES

        sn = SnapshotNode(
            role=role,
            name=name,
            is_interactive=is_interactive,
            is_context=is_context,
        )

        if is_interactive:
            ref = counter[0]
            attrs = _extract_props(node)
            selector_map[ref] = ElementRef(
                index=ref,
                tag=role,
                role=role,
                text=name,
                attributes=attrs,
                depth=0,
                description=attrs.get("description", ""),
            )
            bdid = node.get("backendDOMNodeId")
            if bdid is not None:
                backend_node_map[ref] = int(bdid)
            sn.ref = ref
            sn.attrs = attrs
            counter[0] += 1
        elif is_context or (name and role):
            sn.attrs = _extract_props(node)

        for cid in child_ids:
            sn.children.extend(visit(cid))

        return [sn]

    children: list[SnapshotNode] = []
    for rid in root_ids:
        children.extend(visit(rid))
    return SnapshotNode(role="root", name="", children=children)


def _index_and_find_roots(
    raw_nodes: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    node_by_id: dict[str, dict[str, Any]] = {}
    for raw in raw_nodes:
        nid = raw.get("nodeId", "")
        if nid:
            node_by_id[nid] = raw
    all_child_ids: set[str] = set()
    for raw in raw_nodes:
        for cid in raw.get("childIds", []):
            all_child_ids.add(cid)
    root_ids: list[str] = []
    for raw in raw_nodes:
        nid = raw.get("nodeId", "")
        if nid and nid not in all_child_ids:
            root_ids.append(nid)
    if not root_ids and raw_nodes:
        root_ids = [raw_nodes[0].get("nodeId", "")]
    return node_by_id, root_ids


# ---------------------------------------------------------------------------
# Phase 2: Dedup Passes
# ---------------------------------------------------------------------------


def _pass_merge_static_text(node: SnapshotNode) -> None:
    """Merge consecutive StaticText children into the first one."""
    for child in node.children:
        _pass_merge_static_text(child)

    i = 0
    while i < len(node.children):
        child = node.children[i]
        if child.role == "StaticText" and child.name and not child.pruned:
            texts = [child.name]
            j = i + 1
            while j < len(node.children):
                nxt = node.children[j]
                if nxt.role == "StaticText" and nxt.name and not nxt.pruned:
                    texts.append(nxt.name)
                    nxt.pruned = True
                    j += 1
                else:
                    break
            if len(texts) > 1:
                child.name = " ".join(texts)
            i = j
        else:
            i += 1


def _pass_dedup_parent_child(node: SnapshotNode) -> None:
    """Remove redundant text where parent name == child text."""
    for child in node.children:
        _pass_dedup_parent_child(child)

    if not node.name or node.pruned:
        return

    active = [c for c in node.children if not c.pruned]

    if node.is_interactive or node.is_context:
        if (
            len(active) == 1
            and active[0].role == "StaticText"
            and _norm(active[0].name) == _norm(node.name)
        ):
            active[0].pruned = True
    else:
        if any(c.is_interactive for c in active):
            return
        _try_prune_text_container(node, active)


def _try_prune_text_container(node: SnapshotNode, active: list[SnapshotNode]) -> None:
    """Prune a non-semantic container whose name duplicates its children's text."""
    child_names: list[str] = []
    for c in active:
        if c.name and not c.is_interactive and not c.is_context:
            child_names.append(c.name)

    if not child_names:
        return

    merged = " ".join(child_names)
    if _norm(merged) == _norm(node.name):
        node.pruned = True
        return

    if (
        len(active) == 1
        and active[0].name
        and _norm(active[0].name) == _norm(node.name)
    ):
        node.pruned = True


def _pass_prune_nested_containers(root: SnapshotNode) -> None:
    """Top-down: prune containers whose name duplicates visible descendant text."""

    def _collect_visible_text(node: SnapshotNode) -> list[str]:
        texts: list[str] = []
        for c in node.children:
            if c.is_interactive:
                continue
            if c.pruned:
                texts.extend(_collect_visible_text(c))
            elif c.name and not c.is_context:
                texts.append(c.name)
            else:
                texts.extend(_collect_visible_text(c))
        return texts

    def _has_interactive_descendant(node: SnapshotNode) -> bool:
        for c in node.children:
            if c.is_interactive and not c.pruned:
                return True
            if _has_interactive_descendant(c):
                return True
        return False

    def walk(node: SnapshotNode) -> None:
        if node.pruned or node.is_interactive or node.is_context:
            for c in node.children:
                walk(c)
            return

        if (
            node.name
            and node.role
            and node.role != "StaticText"
            and not _has_interactive_descendant(node)
        ):
            texts = _collect_visible_text(node)
            if texts:
                merged = " ".join(texts)
                if _norm(merged) == _norm(node.name):
                    node.pruned = True
                    return

        for c in node.children:
            walk(c)

    for c in root.children:
        walk(c)


def _pass_prune_empty(node: SnapshotNode) -> None:
    """Prune non-interactive nodes with all-pruned children and no name."""
    for child in node.children:
        _pass_prune_empty(child)

    if node.pruned or node.is_interactive:
        return

    if not node.name and node.children and all(c.pruned for c in node.children):
        node.pruned = True


# ---------------------------------------------------------------------------
# Compact mode: tree pruning
# ---------------------------------------------------------------------------


def _compact_mark(node: SnapshotNode) -> bool:
    """Mark non-interactive nodes without interactive descendants as pruned.

    Returns True if this subtree contains at least one interactive node.
    """
    has_interactive = node.is_interactive and not node.pruned

    for child in node.children:
        if _compact_mark(child):
            has_interactive = True

    if not has_interactive and not node.pruned:
        node.pruned = True

    return has_interactive


# ---------------------------------------------------------------------------
# Phase 3: Flatten + Render
# ---------------------------------------------------------------------------


def _count_all_nodes(root: SnapshotNode) -> int:
    """Count all non-root nodes (including pruned) for total_nodes stat."""
    count = 0
    for c in root.children:
        count += 1 + _count_all_nodes(c)
    return count


def _flatten(
    root: SnapshotNode,
    *,
    mode: str,
) -> list[tuple[int, str, int | None]]:
    content_mode = mode == "content"
    compact = mode == "compact"
    lines: list[tuple[int, str, int | None]] = []

    def walk(node: SnapshotNode, depth: int) -> None:
        if node.pruned:
            for child in node.children:
                walk(child, depth)
            return

        if node.role == "root":
            for child in node.children:
                walk(child, depth)
            return

        if node.role == "frame":
            lines.append((depth, f'[frame "{node.name}"]', None))
            for child in node.children:
                walk(child, depth + 1)
            return

        if content_mode:
            if node.name:
                lines.append((depth, node.name, node.ref))
        elif node.is_interactive:
            attr_str = _format_attrs(node.attrs)
            line = f"[{node.ref}] {node.role}"
            if node.name:
                line += f' "{node.name}"'
            if attr_str:
                line += f" {attr_str}"
            lines.append((depth, line, node.ref))
        elif node.is_context and node.name:
            attr_str = _format_attrs(node.attrs)
            line = f'{node.role} "{node.name}"'
            if attr_str:
                line += f" {attr_str}"
            lines.append((depth, line, None))
        elif node.role == "StaticText" and node.name:
            lines.append((depth, node.name, None))
        elif not compact and node.name and node.role:
            attr_str = _format_attrs(node.attrs)
            line = f'{node.role} "{node.name}"'
            if attr_str:
                line += f" {attr_str}"
            lines.append((depth, line, None))

        for child in node.children:
            walk(child, depth + 1)

    walk(root, 0)
    return lines


# ---------------------------------------------------------------------------
# build_snapshot — main entry point
# ---------------------------------------------------------------------------


def build_snapshot(
    raw_nodes: list[dict[str, Any]],
    *,
    mode: str = "accessible",
    max_nodes: int = 0,
    max_chars: int = 0,
    focus: int = 0,
    offset: int = 0,
    seq: int = 0,
    url: str = "",
    title: str = "",
    frame_trees: list[FrameData] | None = None,
) -> SnapshotResult:
    selector_map: dict[int, ElementRef] = {}
    backend_node_map: dict[int, int] = {}
    counter = [1]
    compact = mode == "compact"
    content_mode = mode == "content"

    # Phase 1: Build tree
    node_by_id, root_ids = _index_and_find_roots(raw_nodes)
    root = _build_tree(
        node_by_id,
        root_ids,
        selector_map=selector_map,
        backend_node_map=backend_node_map,
        counter=counter,
    )

    # Phase 1b: Frame merge
    if frame_trees:
        for frame_data in frame_trees:
            frame_prefix = frame_data.frame_id + ":"
            f_raw: list[dict[str, Any]] = []
            for raw in frame_data.nodes:
                orig_id = raw.get("nodeId", "")
                if not orig_id:
                    continue
                patched = dict(raw)
                patched["nodeId"] = frame_prefix + orig_id
                patched["childIds"] = [
                    frame_prefix + c for c in raw.get("childIds", [])
                ]
                f_raw.append(patched)

            f_node_by_id, f_root_ids = _index_and_find_roots(f_raw)
            frame_subtree = _build_tree(
                f_node_by_id,
                f_root_ids,
                selector_map=selector_map,
                backend_node_map=backend_node_map,
                counter=counter,
            )

            frame_label = frame_data.name or frame_data.url or frame_data.frame_id
            frame_ctx = SnapshotNode(
                role="frame",
                name=frame_label,
                is_context=True,
                children=frame_subtree.children,
            )
            root.children.append(frame_ctx)

    # Phase 2: Dedup passes (all modes benefit)
    _pass_merge_static_text(root)
    _pass_dedup_parent_child(root)
    _pass_prune_nested_containers(root)
    _pass_prune_empty(root)

    # Phase 2b: Compact prune
    if compact:
        _compact_mark(root)

    # Phase 3: Flatten
    total_nodes = _count_all_nodes(root)
    total_interactive = len(selector_map)

    all_lines = _flatten(root, mode=mode)

    cached_lines = list(all_lines)

    # Phase 4: Progressive loading (focus / offset / truncation) — unchanged
    output_lines = all_lines

    if focus > 0 and focus in selector_map:
        output_lines = _extract_focus_subtree(all_lines, focus)
    elif offset > 0:
        output_lines = all_lines[offset:]

    truncated_at = 0
    if max_nodes and max_nodes > 0 and len(output_lines) > max_nodes:
        visible = output_lines[:max_nodes]
        remaining = output_lines[max_nodes:]
        truncated_at = max_nodes + (offset if offset > 0 else 0)
        output_lines = visible
        remaining_refs = [r for _, _, r in remaining if r is not None]
        if remaining_refs:
            min_ref = min(remaining_refs)
            max_ref = max(remaining_refs)
            summary = (
                f"--- not shown: [{min_ref}]-[{max_ref}]"
                f" {len(remaining)} elements"
                f" (--focus=N to expand subtree,"
                f" --offset={truncated_at} to page) ---"
            )
        else:
            summary = (
                f"--- not shown: {len(remaining)} elements"
                f" (--offset={truncated_at} to page) ---"
            )
        output_lines = [*output_lines, (0, summary, None)]

    # Render
    rendered: list[str] = []
    if content_mode:
        prev_text: str | None = None
        for _depth, text, _ in output_lines:
            if text == prev_text:
                continue
            rendered.append(text)
            prev_text = text
    else:
        for depth, text, _ in output_lines:
            rendered.append(_INDENT_STEP * depth + text)
    tree_text = "\n".join(rendered)

    if max_chars and max_chars > 0 and len(tree_text) > max_chars:
        cut = tree_text.rfind("\n", 0, max_chars)
        tree_text = tree_text[:cut] if cut > 0 else tree_text[:max_chars]
        tree_text += "\n[...truncated, use --offset or --focus to continue]"

    snapshot = PageSnapshot(
        seq=seq,
        url=url,
        title=title,
        mode=mode,
        tree_text=tree_text,
        selector_map=selector_map,
        total_nodes=total_nodes,
        total_interactive=total_interactive,
        truncated_at=truncated_at,
    )

    return SnapshotResult(
        snapshot=snapshot,
        selector_map=selector_map,
        backend_node_map=backend_node_map,
        cached_lines=cached_lines,
    )


# ---------------------------------------------------------------------------
# Snapshot diff (unchanged)
# ---------------------------------------------------------------------------

CachedLine = tuple[int, str, int | None]
DiffLine = tuple[int, str, int | None, str | None]


def _line_key(line: CachedLine) -> str:
    _depth, text, ref = line
    if ref is not None:
        return f"ref:{ref}"
    return f"ctx:{text}"


def diff_snapshots(
    previous: list[CachedLine],
    current: list[CachedLine],
) -> list[DiffLine]:
    """Compare two snapshot cached_lines lists and mark changes."""
    if not previous:
        return [(d, t, r, "+") for d, t, r in current]

    prev_by_key: dict[str, tuple[int, str]] = {}
    prev_refs: set[int] = set()
    for depth, text, ref in previous:
        key = _line_key((depth, text, ref))
        prev_by_key[key] = (depth, text)
        if ref is not None:
            prev_refs.add(ref)

    result: list[DiffLine] = []
    cur_refs: set[int] = set()

    for depth, text, ref in current:
        if ref is not None:
            cur_refs.add(ref)
        key = _line_key((depth, text, ref))
        prev_entry = prev_by_key.get(key)

        if prev_entry is None:
            result.append((depth, text, ref, "+"))
        elif prev_entry != (depth, text):
            result.append((depth, text, ref, "~"))
        else:
            result.append((depth, text, ref, None))

    removed = sorted(prev_refs - cur_refs)
    if removed:
        refs_str = " ".join(f"[{r}]" for r in removed)
        result.append((0, f"# removed: {refs_str}", None, None))

    return result


def count_diff(diff_lines: list[DiffLine]) -> DiffCounts:
    """Count added, changed, and removed lines in a diff."""
    counts = DiffCounts()
    for _depth, text, _ref, marker in diff_lines:
        if marker == "+":
            counts.added += 1
        elif marker == "~":
            counts.changed += 1
        elif marker is None and text.startswith("# removed:"):
            counts.removed += text.count("[")
    return counts


def render_diff_tree(diff_lines: list[DiffLine]) -> str:
    """Render diff lines into indented tree text with ``[+]``/``[~]`` markers."""
    rendered: list[str] = []
    for depth, text, _ref, marker in diff_lines:
        prefix = _INDENT_STEP * depth
        if marker == "+":
            rendered.append(f"{prefix}[+] {text}")
        elif marker == "~":
            rendered.append(f"{prefix}[~] {text}")
        else:
            rendered.append(f"{prefix}{text}")
    return "\n".join(rendered)


def truncate_diff_lines(
    diff_lines: list[DiffLine],
    *,
    max_nodes: int,
    offset: int = 0,
) -> tuple[list[DiffLine], int]:
    """Apply node-level truncation to a diff line list."""
    if not max_nodes or max_nodes <= 0 or len(diff_lines) <= max_nodes:
        return diff_lines, 0

    visible = diff_lines[:max_nodes]
    remaining = diff_lines[max_nodes:]
    truncated_at = max_nodes + (offset if offset > 0 else 0)

    remaining_refs = [r for _, _, r, _ in remaining if r is not None]
    if remaining_refs:
        min_ref = min(remaining_refs)
        max_ref = max(remaining_refs)
        summary = (
            f"--- not shown: [{min_ref}]-[{max_ref}]"
            f" {len(remaining)} elements"
            f" (--focus=N to expand subtree,"
            f" --offset={truncated_at} to page) ---"
        )
    else:
        summary = (
            f"--- not shown: {len(remaining)} elements"
            f" (--offset={truncated_at} to page) ---"
        )
    visible = [*visible, (0, summary, None, None)]
    return visible, truncated_at
