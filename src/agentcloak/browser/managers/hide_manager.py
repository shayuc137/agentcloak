"""Persistent CSS hiding shared by every browser backend."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterable

    from agentcloak.browser.base import BrowserContextBase

__all__ = ["HideManager"]


class HideManager:
    """Manage persistent and observation-scoped CSS hiding."""

    BUILTIN = "[data-cloak-hide]"
    STYLE_ID = "__cloak_hide__"
    BUILTIN_ID = "builtin"

    def __init__(self, ctx: BrowserContextBase) -> None:
        self._ctx = ctx
        self._selectors: dict[str, str] = {self.BUILTIN_ID: self.BUILTIN}
        self._script_identifier: str | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _selector_id(selector: str) -> str:
        digest = hashlib.sha256(selector.encode()).hexdigest()[:12]
        return f"hide-{digest}"

    async def add(self, selector: str) -> str:
        """Add a selector and apply the rebuilt stylesheet immediately."""
        normalized = selector.strip()
        if not normalized:
            raise ValueError("hide selector must not be empty")
        identifier = self._selector_id(normalized)
        async with self._lock:
            self._selectors[identifier] = normalized
            await self._apply_locked()
        return identifier

    async def remove(self, identifier_or_selector: str) -> bool:
        """Remove a user selector by id or exact selector text."""
        value = identifier_or_selector.strip()
        async with self._lock:
            identifier = value if value in self._selectors else ""
            if not identifier:
                identifier = next(
                    (
                        key
                        for key, selector in self._selectors.items()
                        if selector == value
                    ),
                    "",
                )
            if not identifier or identifier == self.BUILTIN_ID:
                return False
            del self._selectors[identifier]
            await self._apply_locked()
            return True

    def list_selectors(self) -> list[dict[str, object]]:
        """Return active selectors with the immutable builtin marked."""
        return [
            {
                "identifier": identifier,
                "selector": selector,
                "builtin": identifier == self.BUILTIN_ID,
            }
            for identifier, selector in self._selectors.items()
        ]

    def persistent_selectors(self) -> list[str]:
        """Return selectors suitable for profile persistence."""
        return [
            selector
            for identifier, selector in self._selectors.items()
            if identifier != self.BUILTIN_ID
        ]

    async def load(self, selectors: Iterable[str]) -> None:
        """Replace user selectors from profile state and apply once."""
        loaded: dict[str, str] = {self.BUILTIN_ID: self.BUILTIN}
        for raw in selectors:
            selector = raw.strip()
            if selector and selector != self.BUILTIN:
                loaded[self._selector_id(selector)] = selector
        async with self._lock:
            self._selectors = loaded
            await self._apply_locked()

    def css_for(
        self,
        extra: Iterable[str] | None = None,
        *,
        keep_overlays: bool = False,
    ) -> str | None:
        """Build the stylesheet, or ``None`` when hiding is disabled."""
        if keep_overlays:
            return None
        selectors = list(self._selectors.values())
        if extra is not None:
            selectors.extend(selector.strip() for selector in extra if selector.strip())
        selectors = list(dict.fromkeys(selectors))
        return f"{', '.join(selectors)} {{ display: none !important; }}"

    async def apply(self) -> None:
        """Register the init script and inject the current page immediately."""
        async with self._lock:
            await self._apply_locked()

    async def _apply_locked(self) -> None:
        css = self.css_for() or ""
        source = self._injection_js(css)
        if self._script_identifier:
            await self._ctx._cdp_send(
                "Page.removeScriptToEvaluateOnNewDocument",
                {"identifier": self._script_identifier},
            )
        result = await self._ctx._cdp_send(
            "Page.addScriptToEvaluateOnNewDocument", {"source": source}
        )
        identifier = str(result.get("identifier", ""))
        self._script_identifier = identifier or None
        await self._ctx._evaluate_impl(source, world="main")

    async def on_navigated(self) -> None:
        """Ensure the new document has the active style."""
        async with self._lock:
            if self._script_identifier is None:
                await self._apply_locked()
                return
            await self._ctx._evaluate_impl(
                self._injection_js(self.css_for() or ""), world="main"
            )

    async def on_tab_switched(self) -> None:
        """Register the style script on a newly active tab."""
        async with self._lock:
            self._script_identifier = None
            await self._apply_locked()

    @asynccontextmanager
    async def observation(
        self,
        *,
        extra: Iterable[str] | None = None,
        keep_overlays: bool = False,
    ) -> AsyncGenerator[None]:
        """Temporarily adjust hiding for one snapshot or screenshot."""
        async with self._lock:
            if self._script_identifier is None:
                await self._apply_locked()
            if keep_overlays:
                await self._ctx._evaluate_impl(self._toggle_js(True), world="main")
            elif extra:
                await self._ctx._evaluate_impl(
                    self._injection_js(self.css_for(extra) or ""), world="main"
                )
            try:
                yield
            finally:
                await self._ctx._evaluate_impl(
                    self._injection_js(self.css_for() or ""), world="main"
                )

    @classmethod
    def _injection_js(cls, css: str) -> str:
        style_id = json.dumps(cls.STYLE_ID)
        css_value = json.dumps(css)
        return (
            "(() => {"
            f"const id={style_id};"
            "let style=document.getElementById(id);"
            "if(!style){style=document.createElement('style');style.id=id;"
            "(document.documentElement||document).appendChild(style);}"
            f"style.textContent={css_value};style.disabled=false;"
            "})()"
        )

    @classmethod
    def _toggle_js(cls, disabled: bool) -> str:
        style_id = json.dumps(cls.STYLE_ID)
        value = "true" if disabled else "false"
        return (
            "(() => {"
            f"const style=document.getElementById({style_id});"
            f"if(style)style.disabled={value};"
            "})()"
        )
