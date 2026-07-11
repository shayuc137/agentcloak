"""SecureBrowserContext — IDPI security wrapper around any BrowserContext.

Decorator pattern: wraps an inner :class:`BrowserContextBase` and intercepts
only the methods that need security checks. Everything else (atomic ops,
properties, internal state) is forwarded via ``__getattr__`` so the daemon
sees the same API surface whether or not the security layer is enabled.

Intercepted methods:
- ``navigate`` — Layer 1 domain check
- ``fetch`` — Layer 1 domain check
- ``tab_new`` — Layer 1 domain check (if URL provided)
- ``snapshot`` — Layer 3 untrusted content wrapping + Layer 2 content scan
- ``action`` / ``action_batch`` — Layer 2 element text scan when configured
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcloak.browser.base import BrowserContextBase
    from agentcloak.browser.state import PageSnapshot
    from agentcloak.core.config import AgentcloakConfig
    from agentcloak.core.types import StealthTier

from agentcloak.core.errors import DialogBlockedError, SecurityError
from agentcloak.core.security import (
    check_domain_allowed,
    scan_content,
    wrap_untrusted,
)

__all__ = ["SecureBrowserContext"]

logger = logging.getLogger(__name__)


class SecureBrowserContext:
    """Transparent IDPI security layer wrapping any browser context.

    The wrapper is intentionally not a :class:`BrowserContextBase` subclass —
    it delegates to an inner one. ``__getattr__`` makes the daemon's introspection
    (``_cdp_port``, ``capture_store``, ``_inner``, etc.) work transparently.
    """

    def __init__(self, inner: BrowserContextBase, config: AgentcloakConfig) -> None:
        self._inner = inner
        self._whitelist = config.security.domain_whitelist
        self._blacklist = config.security.domain_blacklist
        self._content_scan = config.security.content_scan
        self._patterns = config.security.content_scan_patterns

    # ------------------------------------------------------------------
    # Layer 1: navigation / fetch / tab_new
    # ------------------------------------------------------------------

    async def navigate(
        self, url: str, *, timeout: float | None = None
    ) -> dict[str, Any]:
        try:
            check_domain_allowed(
                url, whitelist=self._whitelist, blacklist=self._blacklist
            )
        except SecurityError:
            # Security layer rejects this URL before the inner backend ever
            # sees it. The inner ``_page_valid`` flag therefore stays True,
            # which would let a subsequent ``screenshot``/``evaluate``/etc.
            # silently keep operating on the previous page — the exact bug
            # PRD 05-19 targets. Flip the inner flag here so the IDPI scheme
            # block (``file://``, blacklist hit, ...) behaves the same as a
            # network-level navigation failure from the agent's perspective.
            self._inner.mark_page_invalid()
            raise
        # Inner navigate() falls back to its own config if timeout is None.
        return await self._inner.navigate(url, timeout=timeout)

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        body: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        check_domain_allowed(url, whitelist=self._whitelist, blacklist=self._blacklist)
        return await self._inner.fetch(
            url, method=method, body=body, headers=headers, timeout=timeout
        )

    async def tab_new(self, url: str | None = None) -> dict[str, Any]:
        if url:
            check_domain_allowed(
                url, whitelist=self._whitelist, blacklist=self._blacklist
            )
        return await self._inner.tab_new(url)

    async def download_url(self, url: str, *, output_dir: str) -> dict[str, Any]:
        # Direct-URL download fetches server-side with the browser's cookies —
        # the same threat surface as ``fetch``. The inner method already runs
        # the SSRF guard (blocks private/loopback hosts), but that does not
        # enforce the operator's domain policy. Run the Layer 1 whitelist /
        # blacklist check here too so a configured policy can't be bypassed by
        # routing an exfiltration through ``download url`` instead of ``fetch``.
        check_domain_allowed(url, whitelist=self._whitelist, blacklist=self._blacklist)
        return await self._inner.download_url(url, output_dir=output_dir)

    # ------------------------------------------------------------------
    # Layer 3 + Layer 2: snapshot
    # ------------------------------------------------------------------

    async def snapshot(
        self,
        *,
        mode: str = "accessible",
        max_nodes: int = 0,
        max_chars: int = 0,
        focus: int = 0,
        offset: int = 0,
        frames: bool = False,
        selector: str = "",
    ) -> PageSnapshot:
        snap: PageSnapshot = await self._inner.snapshot(
            mode=mode,
            max_nodes=max_nodes,
            max_chars=max_chars,
            focus=focus,
            offset=offset,
            frames=frames,
            selector=selector,
        )

        warnings: list[dict[str, str | int]] = []
        if self._content_scan and self._patterns:
            matches = scan_content(snap.tree_text, self._patterns)
            if matches:
                warnings = [m.to_dict() for m in matches]
                logger.warning(
                    "content_scan_warnings",
                    extra={"url": snap.url, "match_count": len(matches)},
                )

        wrapped_text = wrap_untrusted(
            snap.tree_text, snap.url, whitelist=self._whitelist
        )

        return replace(
            snap,
            tree_text=wrapped_text,
            security_warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Layer 2: action element scan
    # ------------------------------------------------------------------

    async def action(self, kind: str, target: str, **kw: Any) -> dict[str, Any]:
        if self._content_scan and self._patterns:
            snap: PageSnapshot = await self._inner.snapshot(mode="accessible")
            self._check_action_target(snap, target)
        return await self._inner.action(kind, target, **kw)

    async def action_batch(
        self,
        actions: list[dict[str, Any]],
        *,
        sleep: float = 0.0,
        settle_timeout: int | None = None,
    ) -> dict[str, Any]:
        if not self._content_scan or not self._patterns:
            return await self._inner.action_batch(
                actions, sleep=sleep, settle_timeout=settle_timeout
            )

        results: list[dict[str, Any]] = []
        total = len(actions)
        if total == 0:
            return {"results": [], "completed": 0, "total": 0}

        for i, act in enumerate(actions):
            kind = act.get("kind", act.get("action", ""))
            index = act.get("index")
            target = str(index) if index is not None else act.get("target", "")

            try:
                snap = await self._inner.snapshot(mode="accessible")
                self._check_action_target(snap, target)
            except SecurityError:
                return {
                    "results": results,
                    "completed": i,
                    "total": total,
                    "aborted_reason": "content_scan_blocked",
                    "blocked_action_index": i,
                }

            extra = {
                k: v
                for k, v in act.items()
                if k not in ("kind", "action", "index", "target")
            }
            try:
                result = await self._inner.action(str(kind), str(target), **extra)
            except DialogBlockedError as exc:
                # Same partial-result semantics as ``BrowserContextBase`` —
                # surface the dialog metadata so the agent can call
                # ``dialog accept/dismiss`` and resume the remaining steps.
                blocked = exc.to_dict()
                blocked["seq"] = getattr(self._inner, "seq", 0)
                results.append(blocked)
                return {
                    "results": results,
                    "completed": i,
                    "total": total,
                    "aborted_reason": "dialog_pending",
                    "dialog": exc.dialog,
                }
            results.append(result)

            if result.get("caused_navigation"):
                return {
                    "results": results,
                    "completed": i + 1,
                    "total": total,
                    "aborted_reason": "url_changed",
                }

            if sleep > 0 and i < total - 1:
                import asyncio

                await asyncio.sleep(sleep)

        return {"results": results, "completed": total, "total": total}

    # ------------------------------------------------------------------
    # Resume snapshot — delegate to inner (no security wrapping needed)
    # ------------------------------------------------------------------

    async def resume_snapshot(self) -> dict[str, Any]:
        return await self._inner.resume_snapshot()

    # ------------------------------------------------------------------
    # Properties — forward explicitly so type checkers and explicit attribute
    # access work cleanly (``__getattr__`` only fires on miss).
    # ------------------------------------------------------------------

    @property
    def seq(self) -> int:
        return self._inner.seq

    @property
    def stealth_tier(self) -> StealthTier:
        return self._inner.stealth_tier

    def browser_description(self) -> str:
        """Forward to the wrapped backend — the security layer is transparent."""
        return self._inner.browser_description()

    @property
    def capture_store(self) -> Any:
        # The base class always exposes a ``capture_store``, so this is safe
        # against either concrete adapter.
        return self._inner.capture_store

    # ------------------------------------------------------------------
    # Catch-all delegation
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # Note: ``__getattr__`` is only called when normal attribute lookup
        # fails, so explicitly declared methods (navigate/fetch/etc.) shadow
        # this proxy correctly.
        return getattr(self._inner, name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_action_target(self, snap: PageSnapshot, target: str) -> None:
        """Check the target element text for injection patterns."""
        try:
            index = int(target)
        except (ValueError, TypeError):
            return

        elem = snap.selector_map.get(index)
        if elem is None:
            return

        text = elem.text or ""
        matches = scan_content(text, self._patterns)
        if matches:
            descriptions = ", ".join(m.pattern for m in matches)
            raise SecurityError(
                error="content_scan_blocked",
                hint=f"Element [{index}] matched injection pattern: {descriptions}",
                action="inspect the element manually or disable content_scan",
            )
