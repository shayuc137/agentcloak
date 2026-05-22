"""Reverse-engineering evaluate presets (Phase 6f R3).

Common reverse-engineering chores — pulling Vue/React component state, decoding
JWTs out of storage, dumping cookies — are otherwise hand-written JS that an
agent retypes (and mistypes) every session. These presets package them as ready
-to-run snippets the ``/evaluate`` route can substitute for the ``js`` body.

Unlike init-script presets (which run before every navigate), an evaluate
preset executes *once* and returns its result inline. Each preset is an IIFE
wrapped in try/catch so a failure surfaces as ``{"error": "..."}`` JSON instead
of a raw page exception, and every preset finishes with ``JSON.stringify`` so
the agent gets a parseable string regardless of the page's serialisation quirks.

All presets must run in the page's main world (``world="main"``) — they reach
for page-framework globals (``__vue__``, ``__reactFiber$``) and document state
that an isolated world can't see. The route forces ``world="main"`` whenever a
preset is requested.
"""

from __future__ import annotations

from agentcloak.core.errors import BackendError

__all__ = ["EVALUATE_PRESETS", "get_preset_js"]


# vue_inspect — walk every element looking for Vue 2 (``__vue__``) and Vue 3
# (``__vue_app__``) handles, then surface the data/method/computed key names so
# the agent knows what the component exposes without dumping (potentially huge)
# values.
_VUE_INSPECT = """
(() => {
  try {
    const out = [];
    const seen = new Set();
    const els = document.querySelectorAll('*');
    let scanned = 0;
    for (const el of els) {
      if (out.length >= 50) break;
      // Vue 2: each component instance hangs off el.__vue__
      const v2 = el.__vue__;
      if (v2 && !seen.has(v2)) {
        seen.add(v2);
        const opts = v2.$options || {};
        out.push({
          selector: el.tagName.toLowerCase()
            + (el.id ? '#' + el.id : '')
            + (el.className && typeof el.className === 'string'
                ? '.' + el.className.trim().split(/\\s+/).join('.') : ''),
          version: 2,
          name: opts.name || opts._componentTag || null,
          data_keys: v2.$data ? Object.keys(v2.$data) : [],
          props_keys: v2.$props ? Object.keys(v2.$props) : [],
          methods: opts.methods ? Object.keys(opts.methods) : [],
          computed: opts.computed ? Object.keys(opts.computed) : [],
        });
      }
      // Vue 3: the app handle lives on the mount root as __vue_app__
      const app3 = el.__vue_app__;
      if (app3 && !seen.has(app3)) {
        seen.add(app3);
        let dataKeys = [];
        try {
          const inst = app3._instance;
          if (inst && inst.data) dataKeys = Object.keys(inst.data);
          else if (inst && inst.setupState)
            dataKeys = Object.keys(inst.setupState);
        } catch (e) { /* best effort */ }
        out.push({
          selector: el.tagName.toLowerCase()
            + (el.id ? '#' + el.id : ''),
          version: 3,
          name: null,
          data_keys: dataKeys,
          props_keys: [],
          methods: [],
          computed: [],
        });
      }
      scanned++;
    }
    return JSON.stringify({
      detected: out.length > 0,
      scanned: scanned,
      components: out,
    });
  } catch (e) {
    return JSON.stringify({ error: String(e && e.message || e) });
  }
})()
"""


# react_inspect — find the React root (``_reactRootContainer`` for legacy
# render, ``__reactContainer$*`` / ``__reactFiber$*`` for React 18+) and walk a
# shallow slice of the fiber tree, emitting component names plus prop/state key
# names. Depth is capped so a deep tree can't blow the token budget.
_REACT_INSPECT = """
(() => {
  try {
    const MAX_NODES = 60;
    const MAX_DEPTH = 3;
    const out = [];

    function fiberRoot() {
      // React 16/17: ReactDOM.render leaves _reactRootContainer on the node.
      const containers = document.querySelectorAll('*');
      for (const el of containers) {
        if (el._reactRootContainer) {
          const root = el._reactRootContainer;
          if (root._internalRoot && root._internalRoot.current)
            return root._internalRoot.current;
          if (root.current) return root.current;
        }
        // React 18: createRoot stores the fiber under __reactContainer$<id>
        const keys = Object.keys(el);
        const ck = keys.find(k => k.startsWith('__reactContainer$'));
        if (ck && el[ck]) return el[ck];
        const fk = keys.find(k => k.startsWith('__reactFiber$'));
        if (fk && el[fk]) {
          let node = el[fk];
          while (node.return) node = node.return;
          return node;
        }
      }
      return null;
    }

    function nameOf(fiber) {
      const t = fiber.type;
      if (!t) return null;
      if (typeof t === 'string') return t;
      return t.displayName || t.name || null;
    }

    function visit(fiber, depth) {
      if (!fiber || out.length >= MAX_NODES || depth > MAX_DEPTH) return;
      const name = nameOf(fiber);
      // Only report named components (skip anonymous host text wrappers).
      if (name && typeof fiber.type !== 'string') {
        const memo = fiber.memoizedState;
        let stateKeys = [];
        try {
          if (memo && typeof memo === 'object' && !memo.tag)
            stateKeys = Object.keys(memo).slice(0, 20);
        } catch (e) { /* best effort */ }
        out.push({
          name: name,
          props_keys: fiber.memoizedProps
            ? Object.keys(fiber.memoizedProps).slice(0, 20) : [],
          state_keys: stateKeys,
          children_count: 0,
        });
      }
      let count = 0;
      let child = fiber.child;
      while (child) { count++; child = child.sibling; }
      if (out.length > 0 && name && typeof fiber.type !== 'string')
        out[out.length - 1].children_count = count;
      visit(fiber.child, depth + (typeof fiber.type === 'string' ? 0 : 1));
      visit(fiber.sibling, depth);
    }

    const root = fiberRoot();
    if (!root) return JSON.stringify({ detected: false, components: [] });
    visit(root, 0);
    return JSON.stringify({ detected: out.length > 0, components: out });
  } catch (e) {
    return JSON.stringify({ error: String(e && e.message || e) });
  }
})()
"""


# jwt_decode — scan document.cookie + localStorage + sessionStorage for JWT-
# shaped strings and base64url-decode the header + payload. Signatures are not
# verified (no key); ``exp`` is surfaced as a readable timestamp when present.
_JWT_DECODE = """
(() => {
  try {
    const re = /eyJ[A-Za-z0-9_-]+\\.eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]*/g;
    const found = [];

    function b64urlDecode(part) {
      try {
        let s = part.replace(/-/g, '+').replace(/_/g, '/');
        while (s.length % 4) s += '=';
        return JSON.parse(decodeURIComponent(escape(atob(s))));
      } catch (e) { return null; }
    }

    function scan(source, key, value) {
      if (typeof value !== 'string') return;
      const matches = value.match(re);
      if (!matches) return;
      for (const tok of matches) {
        const parts = tok.split('.');
        const header = b64urlDecode(parts[0]);
        const payload = b64urlDecode(parts[1]);
        if (!header && !payload) continue;
        let expReadable = null;
        if (payload && payload.exp)
          expReadable = new Date(payload.exp * 1000).toISOString();
        found.push({
          source: source,
          key: key,
          header: header,
          payload: payload,
          exp_readable: expReadable,
        });
      }
    }

    // Cookies (document.cookie only exposes name=value pairs).
    document.cookie.split(';').forEach(pair => {
      const idx = pair.indexOf('=');
      if (idx === -1) return;
      const name = pair.slice(0, idx).trim();
      const val = pair.slice(idx + 1).trim();
      scan('cookie', name, val);
    });

    for (const store of [['localStorage', localStorage],
                         ['sessionStorage', sessionStorage]]) {
      try {
        for (let i = 0; i < store[1].length; i++) {
          const k = store[1].key(i);
          scan(store[0], k, store[1].getItem(k));
        }
      } catch (e) { /* storage may be blocked */ }
    }

    return JSON.stringify({ count: found.length, tokens: found });
  } catch (e) {
    return JSON.stringify({ error: String(e && e.message || e) });
  }
})()
"""


# cookie_parse — structure document.cookie into name/value objects. httpOnly /
# path / domain are invisible to JS, so this only reports what document.cookie
# can see (use ``cloak cookies export`` for the full set).
_COOKIE_PARSE = """
(() => {
  try {
    const cookies = [];
    if (document.cookie) {
      document.cookie.split(';').forEach(pair => {
        const idx = pair.indexOf('=');
        if (idx === -1) {
          const name = pair.trim();
          if (name) cookies.push({ name: name, value: '' });
          return;
        }
        cookies.push({
          name: pair.slice(0, idx).trim(),
          value: pair.slice(idx + 1).trim(),
        });
      });
    }
    return JSON.stringify({ count: cookies.length, cookies: cookies });
  } catch (e) {
    return JSON.stringify({ error: String(e && e.message || e) });
  }
})()
"""


# storage_dump — export every localStorage + sessionStorage key/value pair.
_STORAGE_DUMP = """
(() => {
  try {
    function dump(store) {
      const out = {};
      try {
        for (let i = 0; i < store.length; i++) {
          const k = store.key(i);
          out[k] = store.getItem(k);
        }
      } catch (e) { /* storage may be blocked */ }
      return out;
    }
    return JSON.stringify({
      localStorage: dump(localStorage),
      sessionStorage: dump(sessionStorage),
    });
  } catch (e) {
    return JSON.stringify({ error: String(e && e.message || e) });
  }
})()
"""


EVALUATE_PRESETS: dict[str, str] = {
    "vue_inspect": _VUE_INSPECT,
    "react_inspect": _REACT_INSPECT,
    "jwt_decode": _JWT_DECODE,
    "cookie_parse": _COOKIE_PARSE,
    "storage_dump": _STORAGE_DUMP,
}


def get_preset_js(name: str) -> str:
    """Return the JS for ``name``, raising on an unknown preset.

    Mirrors the ``config_writer`` unknown-key pattern: the error lists the
    available presets so an agent that mistyped one gets the valid set back in
    a single round-trip instead of guessing.
    """
    js = EVALUATE_PRESETS.get(name)
    if js is None:
        available = ", ".join(sorted(EVALUATE_PRESETS))
        raise BackendError(
            error="unknown_preset",
            hint=f"No evaluate preset named {name!r}",
            action=f"use one of: {available}",
        )
    return js
