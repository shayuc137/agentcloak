"""RouteManager — network route interception (Phase 7b T1.3).

Holds the list of active :class:`RouteRule` objects and decides, for a given
in-flight request, which rule (if any) applies and what to do with it
(``abort`` / ``fulfill`` / ``continue``). The actual interception transport is
backend-specific and lives behind two base atoms:

* ``_route_add_impl(rule)`` — start intercepting matching requests
  (Playwright ``page.route``; RemoteBridge CDP ``Fetch.enable`` + the
  ``Fetch.requestPaused`` handler).
* ``_route_remove_impl(pattern)`` — stop intercepting (``page.unroute`` /
  re-derive the ``Fetch`` patterns), or clear everything when ``pattern`` is
  ``None``.

Keeping the rule list and the matching logic here (rather than in each backend)
means both backends agree on precedence and field semantics; the backends only
own *how* a paused request is resumed. The list also lets ``route list`` report
state and lets a new tab replay the existing rules.
"""

# pyright: reportPrivateUsage=false
# RouteManager reaches the backend only through the base's ``_route_*_impl``
# atoms (design decision D-Q2/D-Q3). They are "protected" to stay off the
# public daemon surface, not to hide them from the manager the base owns.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentcloak.browser.base import BrowserContextBase

__all__ = ["RouteManager", "RouteRule"]


@dataclass
class RouteRule:
    """One network interception rule.

    ``pattern`` is a URL glob (``*`` matches anything; matched as a substring
    when no ``*`` is present). ``action`` decides the disposition:

    * ``abort`` — fail the request (optionally surfacing as a network error).
    * ``fulfill`` — short-circuit with a synthetic response (``status`` /
      ``content_type`` / ``body``).
    * ``continue`` — let it proceed (useful purely for observation, or paired
      with header rewriting later).

    The optional filters (``resource_type`` / ``method``) narrow *which*
    requests the rule applies to; ``status`` / ``content_type`` / ``body`` shape
    a ``fulfill`` response and are ignored for the other actions.
    """

    pattern: str
    action: str  # "abort" | "fulfill" | "continue"
    resource_type: str | None = None
    method: str | None = None
    status: int | None = None
    content_type: str | None = None
    body: str | None = None

    def matches(
        self, url: str, *, resource_type: str | None, method: str | None
    ) -> bool:
        """Return True when this rule applies to the described request."""
        if not _url_matches(self.pattern, url):
            return False
        if (
            self.resource_type
            and resource_type
            and self.resource_type.lower() != resource_type.lower()
        ):
            return False
        return not (self.method and method and self.method.upper() != method.upper())

    def to_dict(self) -> dict[str, object]:
        """Serialise for ``route list`` output (omit unset optional fields)."""
        out: dict[str, object] = {"pattern": self.pattern, "action": self.action}
        if self.resource_type:
            out["resource_type"] = self.resource_type
        if self.method:
            out["method"] = self.method
        if self.status is not None:
            out["status"] = self.status
        if self.content_type:
            out["content_type"] = self.content_type
        if self.body is not None:
            out["body"] = self.body
        return out


def _url_matches(pattern: str, url: str) -> bool:
    """Glob/substring URL match shared by both backends.

    ``*`` is a wildcard for any run of characters; ``**`` behaves the same here
    (we don't distinguish path boundaries for route patterns — interception is
    coarser than navigation matching). A pattern with no ``*`` is treated as a
    plain substring so ``api/login`` matches without the caller wrapping it in
    stars.
    """
    if "*" not in pattern:
        return pattern in url
    import re

    parts = [re.escape(p) for p in pattern.split("*")]
    regex = ".*".join(parts)
    return re.fullmatch(regex, url) is not None


class RouteManager:
    """Own the active route-rule list; delegate transport to the backend."""

    def __init__(self, ctx: BrowserContextBase) -> None:
        self._ctx = ctx
        self._rules: list[RouteRule] = []

    async def add(self, rule: RouteRule) -> None:
        """Register ``rule`` and start intercepting matching requests."""
        self._rules.append(rule)
        await self._ctx._route_add_impl(rule)

    async def remove(self, pattern: str | None) -> int:
        """Remove rules by ``pattern`` (or all when ``None``); return count removed.

        The local list is updated *before* the backend teardown so a backend
        that re-derives its interception state from the live rule list (the
        RemoteBridge ``Fetch`` path checks whether any rules remain) observes
        the post-removal set and can fully disable when the last rule is gone.
        """
        if pattern is None:
            removed = len(self._rules)
            self._rules.clear()
        else:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.pattern != pattern]
            removed = before - len(self._rules)
        await self._ctx._route_remove_impl(pattern)
        return removed

    def list_rules(self) -> list[RouteRule]:
        """Return the active rules in insertion order."""
        return list(self._rules)

    def match(
        self, url: str, *, resource_type: str | None = None, method: str | None = None
    ) -> RouteRule | None:
        """Return the first rule that applies to the request, or ``None``.

        First-match wins by insertion order. We deliberately do *not* mirror
        Playwright's most-recently-registered-handler-first precedence —
        insertion order is simpler for an agent to reason about when reading
        ``route list`` back.
        """
        for rule in self._rules:
            if rule.matches(url, resource_type=resource_type, method=method):
                return rule
        return None

    async def on_tab_switched(self) -> None:
        """Re-register every rule onto the now-active page.

        A fresh page has no Playwright route handlers / ``Fetch`` patterns, so
        replay the existing rules. The local list is the source of truth; we
        only re-issue the transport setup.
        """
        for rule in list(self._rules):
            await self._ctx._route_add_impl(rule)
