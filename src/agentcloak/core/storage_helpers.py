"""JS snippet builders for localStorage / sessionStorage access (7a R4).

Storage is read and written through ``page.evaluate()`` rather than a
dedicated backend ``_impl`` method — both Playwright and RemoteBridge already
expose ``evaluate()``, so a shared JS-template helper keeps the daemon route
backend-agnostic without growing the ABC. The route layer feeds these strings
into ``ctx.evaluate(...)`` and shapes the result into the storage response.

Each builder validates the storage type against a small allow-list so a
crafted ``type`` value can't be interpolated into the ``window.<x>Storage``
member expression (the only place untrusted input touches the JS source).
``key`` / ``value`` are always JSON-encoded before interpolation, so they are
inert string literals in the emitted snippet.
"""

from __future__ import annotations

import orjson

__all__ = [
    "STORAGE_TYPES",
    "build_storage_clear_js",
    "build_storage_delete_js",
    "build_storage_get_js",
    "build_storage_set_js",
    "normalize_storage_type",
]

# The two web-storage areas. ``local`` survives across sessions; ``session``
# is per-tab. Anything else is rejected before it reaches the JS template.
STORAGE_TYPES = frozenset({"local", "session"})


def normalize_storage_type(storage_type: str) -> str:
    """Return the validated storage area name (``local`` or ``session``).

    Raises :class:`ValueError` for anything outside :data:`STORAGE_TYPES`.
    The caller (daemon route) converts that into the structured error
    envelope; keeping the guard here means every JS builder is safe to call
    on the normalized value.
    """
    normalized = (storage_type or "local").lower()
    if normalized not in STORAGE_TYPES:
        raise ValueError(
            f"storage type must be one of {sorted(STORAGE_TYPES)}, got {storage_type!r}"
        )
    return normalized


def _quote(value: str) -> str:
    """JSON-encode a string for safe interpolation into a JS snippet."""
    return orjson.dumps(value).decode()


def build_storage_get_js(storage_type: str, key: str | None) -> str:
    """JS that returns a single value (when ``key`` given) or all entries.

    With no ``key`` the snippet returns an object of every key/value pair so
    the route can render the whole store; with a ``key`` it returns the bare
    string value (or ``null`` when the key is absent).
    """
    area = normalize_storage_type(storage_type)
    member = f"window.{area}Storage"
    if key is not None:
        return f"{member}.getItem({_quote(key)})"
    # ``Object.fromEntries`` keeps the result a plain JSON object the daemon
    # can hand straight to orjson without per-key marshalling.
    return (
        f"Object.fromEntries(Object.keys({member})"
        f".map((k) => [k, {member}.getItem(k)]))"
    )


def build_storage_set_js(storage_type: str, key: str, value: str) -> str:
    """JS that writes ``key=value`` into the chosen storage area."""
    area = normalize_storage_type(storage_type)
    return f"window.{area}Storage.setItem({_quote(key)}, {_quote(value)})"


def build_storage_delete_js(storage_type: str, key: str) -> str:
    """JS that removes a single ``key`` from the chosen storage area."""
    area = normalize_storage_type(storage_type)
    return f"window.{area}Storage.removeItem({_quote(key)})"


def build_storage_clear_js(storage_type: str) -> str:
    """JS that empties the chosen storage area."""
    area = normalize_storage_type(storage_type)
    return f"window.{area}Storage.clear()"
