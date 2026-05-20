"""Browser state data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentcloak.core.types import StealthTier

__all__ = [
    "CONTEXT_ROLES",
    "INTERACTIVE_ROLES",
    "BrowserState",
    "ConsoleEntry",
    "DownloadEntry",
    "ElementRef",
    "FrameInfo",
    "PageInfo",
    "PageSnapshot",
    "PendingDialog",
    "TabInfo",
]

INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "link",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "radio",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
        "treeitem",
        # R3: expanded interactive roles
        "dialog",
        "alertdialog",
        "menu",
        "listbox",
        "tree",
        "grid",
    }
)

CONTEXT_ROLES = frozenset(
    {
        "toolbar",
        "tabpanel",
        "figure",
        "table",
        "form",
        "status",
        "alert",
        # Structural landmark roles (always show in tree for context)
        "heading",
        "banner",
        "navigation",
        "main",
        "region",
        "contentinfo",
        "complementary",
        "search",
    }
)


@dataclass(frozen=True)
class PendingDialog:
    """A dialog that is waiting for agent handling."""

    dialog_type: str
    message: str
    default_value: str = ""
    url: str = ""


@dataclass(frozen=True)
class ConsoleEntry:
    """A captured console message or uncaught page error.

    Carries its own monotonic ``seq`` (independent of the action sequence
    counter) so ``console_entries(since=N)`` can page through messages the
    same way ``network --since`` does. ``is_error`` distinguishes
    ``page.on('pageerror')`` (uncaught exceptions) from ordinary
    ``console.*`` calls so agents can filter to real failures.
    """

    seq: int
    level: str
    text: str
    timestamp: float
    url: str = ""
    line: int | None = None
    column: int | None = None
    is_error: bool = False


@dataclass(frozen=True)
class DownloadEntry:
    """A completed download saved to local disk."""

    filename: str
    path: str
    size: int
    url: str = ""
    source: str = "url"  # "url" (direct httpx) | "event" (click-triggered)


@dataclass(frozen=True)
class FrameInfo:
    """Metadata for a page frame."""

    name: str
    url: str
    is_current: bool = False


@dataclass(frozen=True)
class TabInfo:
    """Metadata for an open browser tab."""

    tab_id: int
    url: str
    title: str
    active: bool


@dataclass(frozen=True)
class ElementRef:
    """A reference to an interactive element in the selector_map."""

    index: int
    tag: str
    role: str
    text: str
    attributes: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    depth: int = 0
    description: str = ""


@dataclass(frozen=True)
class PageInfo:
    """Metadata about the current page."""

    url: str
    title: str
    load_state: str


@dataclass(frozen=True)
class PageSnapshot:
    """A snapshot of page state in a given mode."""

    seq: int
    url: str
    title: str
    mode: str
    tree_text: str
    selector_map: dict[int, ElementRef] = field(
        default_factory=lambda: dict[int, ElementRef]()
    )
    security_warnings: list[dict[str, str | int]] = field(
        default_factory=lambda: list[dict[str, str | int]]()
    )
    total_nodes: int = 0
    total_interactive: int = 0
    truncated_at: int = 0


@dataclass
class BrowserState:
    """Full observable state of a browser session."""

    seq: int
    url: str
    title: str
    tabs: list[TabInfo]
    selector_map: dict[int, ElementRef]
    tree_text: str
    screenshot_b64: str | None
    screenshot_size: tuple[int, int] | None
    viewport_size: tuple[int, int]
    page_info: PageInfo
    pending_network_requests: list[dict[str, object]]
    recent_events_text: str | None
    stealth_tier: StealthTier
