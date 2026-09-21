"""ScriptManager — init-script injection via CDP (Phase 7b T1.1).

Wraps ``Page.addScriptToEvaluateOnNewDocument`` / ``...removeScript...`` so an
agent can inject JavaScript that runs *before* any page script on every
navigation. This is the standard hook point for reverse engineering: patch
``fetch``/``XMLHttpRequest``/``JSON.parse`` before the page can use them, so the
hook sees the very first call.

``evaluate`` runs after the page has loaded and only once; init scripts run on
every document and run first. The two are complementary — this manager fills
the "before page script" gap evaluate can't reach.

The manager keeps a ``identifier -> source`` map so ``list`` can report what's
active and so a tab switch can replay the same scripts onto a fresh page (a new
tab inherits none of the previous tab's init scripts). All browser access goes
through the base's thin CDP funnel (``ctx._cdp_send``); the manager never
touches a backend session directly.
"""

# pyright: reportPrivateUsage=false
# Managers are an intentional extension of BrowserContextBase: they reach the
# browser exclusively through the base's thin CDP funnel (``_cdp_send`` etc.),
# which is the documented collaboration (design decision D-Q3). Those names are
# "protected" to keep them off the public daemon surface, not to hide them from
# the managers that the base itself constructs.

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

from agentcloak.core.errors import BackendError

if TYPE_CHECKING:
    from agentcloak.browser.base import BrowserContextBase

__all__ = ["PRESET_TEMPLATES", "ScriptManager"]


# --- Hook presets -----------------------------------------------------------
# Small, self-contained JS snippets that wrap a hot interception point and log
# through ``console`` (which agentcloak already captures via ``cloak console``).
# Each is wrapped in an IIFE with a guard flag so re-injection after a tab
# switch is harmless. Kept deliberately compact — these are starting points an
# agent can read back via ``script list`` and adapt, not a full framework.

_HOOK_FETCH = """
(() => {
  if (window.__cloak_hook_fetch) return;
  window.__cloak_hook_fetch = true;
  const orig = window.fetch;
  window.fetch = function (...args) {
    try {
      console.log('[cloak:fetch] ' + (args[0] && args[0].url || args[0]));
    } catch (e) {}
    return orig.apply(this, args);
  };
})();
"""

_HOOK_XHR = """
(() => {
  if (window.__cloak_hook_xhr) return;
  window.__cloak_hook_xhr = true;
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    try {
      console.log('[cloak:xhr] ' + method + ' ' + url);
    } catch (e) {}
    return open.call(this, method, url, ...rest);
  };
})();
"""

_HOOK_JSON_PARSE = """
(() => {
  if (window.__cloak_hook_json) return;
  window.__cloak_hook_json = true;
  const orig = JSON.parse;
  JSON.parse = function (text, reviver) {
    try {
      console.log('[cloak:json.parse] ' + String(text).slice(0, 120));
    } catch (e) {}
    return orig.call(this, text, reviver);
  };
})();
"""

_HOOK_CRYPTO = """
(() => {
  if (window.__cloak_hook_crypto || !window.crypto || !crypto.subtle) return;
  window.__cloak_hook_crypto = true;
  const orig = crypto.subtle.digest;
  crypto.subtle.digest = function (algo, data) {
    try {
      console.log('[cloak:crypto.digest] ' + (algo && algo.name || algo));
    } catch (e) {}
    return orig.call(this, algo, data);
  };
})();
"""

_HOOK_TIMING = """
(() => {
  if (window.__cloak_hook_timing) return;
  window.__cloak_hook_timing = true;
  const st = window.setTimeout;
  window.setTimeout = function (fn, delay, ...rest) {
    try {
      console.log('[cloak:setTimeout] delay=' + delay);
    } catch (e) {}
    return st.call(this, fn, delay, ...rest);
  };
})();
"""

PRESET_TEMPLATES: dict[str, str] = {
    "fetch": _HOOK_FETCH,
    "xhr": _HOOK_XHR,
    "json_parse": _HOOK_JSON_PARSE,
    "crypto": _HOOK_CRYPTO,
    "timing": _HOOK_TIMING,
}


class ScriptManager:
    """Manage init scripts injected via ``Page.addScriptToEvaluateOnNewDocument``."""

    def __init__(self, ctx: BrowserContextBase) -> None:
        self._ctx = ctx
        # identifier -> JS source. Both the CDP-assigned identifier and the raw
        # source are kept so ``list`` is informative and ``on_tab_switched`` can
        # replay the same sources onto a new page.
        self._scripts: dict[str, str] = {}
        self._markers: dict[str, str] = {}

    async def add(self, js: str) -> str:
        """Inject ``js`` as an init script; return its CDP identifier."""
        return await self._register(js, "__cloak_script_" + uuid4().hex)

    async def _register(self, js: str, marker: str) -> str:
        await self._ctx._cdp_enable_domain("Page")
        key = json.dumps(marker)
        source = f"{js}\n;globalThis[{key}] = 'injected';"
        result = await self._ctx._cdp_send(
            "Page.addScriptToEvaluateOnNewDocument", {"source": source}
        )
        identifier = str(result.get("identifier", ""))
        if not identifier:
            raise BackendError(
                error="script_registration_failed",
                hint="Browser returned no script identifier",
                action="check the browser CDP connection and retry",
            )
        self._scripts[identifier] = js
        self._markers[identifier] = marker
        return identifier

    async def add_preset(self, preset: str) -> str:
        """Inject a built-in hook preset by name.

        Raises ``KeyError`` if ``preset`` is unknown; the route layer turns that
        into a structured error so the agent gets the valid preset list back.
        """
        js = PRESET_TEMPLATES[preset]
        return await self.add(js)

    async def remove(self, identifier: str) -> bool:
        """Remove a previously-added init script by identifier.

        Returns ``True`` when the identifier was known to this manager. The CDP
        call is best-effort — Chrome ignores an unknown identifier — but we only
        report success for ones we actually tracked so ``list`` stays truthful.
        """
        await self._ctx._cdp_send(
            "Page.removeScriptToEvaluateOnNewDocument", {"identifier": identifier}
        )
        self._markers.pop(identifier, None)
        return self._scripts.pop(identifier, None) is not None

    def list_scripts(self) -> dict[str, str]:
        """Return a copy of the ``identifier -> source`` map."""
        return dict(self._scripts)

    async def on_tab_switched(self) -> None:
        """Replay tracked init scripts onto the now-active page.

        A new tab/page has none of the previously-injected scripts, so after a
        switch we re-add each source. The CDP call returns a fresh identifier;
        we drop the stale keys and rebuild the map so ``remove``/``list`` keep
        working against the live page.
        """
        if not self._scripts:
            return
        sources = [
            (js, self._markers[identifier]) for identifier, js in self._scripts.items()
        ]
        self._scripts.clear()
        self._markers.clear()
        for js, marker in sources:
            await self._register(js, marker)

    async def detach(self) -> None:
        for identifier in self._scripts:
            await self._ctx._cdp_send(
                "Page.removeScriptToEvaluateOnNewDocument", {"identifier": identifier}
            )

    async def statuses(self) -> dict[str, str]:
        if not self._markers:
            return {}
        expression = (
            "JSON.stringify(Object.fromEntries("
            + json.dumps(list(self._markers.items()))
            + ".map(([id,key])=>[id,globalThis[key] || 'not injected'])))"
        )
        raw = await self._ctx.evaluate(expression, world="main")
        return json.loads(raw)
