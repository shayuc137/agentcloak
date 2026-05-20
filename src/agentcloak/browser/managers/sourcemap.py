"""SourceMapManager — source map discovery, parsing, position lookup (7b T4).

Closes the reverse-engineering loop: T3's :class:`DebuggerManager` records every
``scriptParsed`` event (including its declared ``sourceMapURL``); this manager
turns that URL into a parsed source map so an agent can map a compiled
line:column back to the *original* file + position and pull the original source
text. Minified/obfuscated bundles become navigable.

Everything is pure Python — the base64-VLQ decoder and the mappings parser are
implemented here rather than shelling out to a Node.js toolchain, keeping the
nothing-but-Python dependency story (design decision D23). The only browser
access is :meth:`get_map` downloading a ``.map`` URL through the base ``fetch``
(so it carries the page's cookies); ``data:`` URIs are decoded inline with no
network round-trip.

Caching: a parsed source map is keyed by ``scriptId`` and memoised, so repeated
``lookup`` / ``sources`` / ``source-content`` calls against the same script pay
the download+parse cost once. The cache is page-scoped in spirit — the debugger
clears its script inventory on navigation, so a stale ``scriptId`` simply stops
resolving and the cache entry becomes unreachable.
"""

# pyright: reportPrivateUsage=false
# SourceMapManager is an intentional extension of BrowserContextBase, mirroring
# the other 7b managers: it reaches the debugger's script inventory
# (``ctx.debugger._scripts``) and the browser only through the base's public
# ``fetch``. The script-dict access is the documented T3→T4 hand-off (the
# debugger's module docstring calls this out explicitly).

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import structlog

from agentcloak.core.errors import AgentBrowserError

if TYPE_CHECKING:
    from agentcloak.browser.base import BrowserContextBase

__all__ = [
    "ParsedSourceMap",
    "SourceMapManager",
    "SourceMapping",
    "decode_vlq",
    "parse_source_map",
]

logger = structlog.get_logger()

# Base64 alphabet used by the source-map VLQ encoding (RFC-4648 standard, *not*
# URL-safe). Index = the 6-bit value a character encodes.
_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_B64_MAP: dict[str, int] = {c: i for i, c in enumerate(_B64)}

# VLQ continuation bit (0x20) flags "more digits follow"; the low 5 bits (0x1F)
# carry payload. The least-significant payload bit of the assembled value is the
# sign, the rest is the magnitude.
_VLQ_CONTINUATION = 0x20
_VLQ_MASK = 0x1F
_VLQ_SHIFT = 5


def decode_vlq(segment: str) -> list[int]:
    """Decode a base64-VLQ ``segment`` into its list of signed integers.

    A mappings segment packs 1, 4, or 5 variable-length quantities back to back
    (generated-column [, source-index, original-line, original-column [,
    name-index]]). Each quantity is little-endian base-32 with a continuation
    bit; the final assembled value's low bit is its sign. Unknown characters
    raise so a corrupt map fails loudly rather than silently mis-decoding.
    """
    values: list[int] = []
    shift = 0
    value = 0
    for ch in segment:
        digit = _B64_MAP.get(ch)
        if digit is None:
            raise AgentBrowserError(
                error="sourcemap_decode_failed",
                hint=f"Invalid base64-VLQ character {ch!r} in mappings",
                action="the source map is malformed; verify the .map file",
            )
        value += (digit & _VLQ_MASK) << shift
        if digit & _VLQ_CONTINUATION:
            shift += _VLQ_SHIFT
            continue
        # Terminal digit: split off the sign bit and emit.
        sign_negative = value & 1
        magnitude = value >> 1
        values.append(-magnitude if sign_negative else magnitude)
        shift = 0
        value = 0
    return values


@dataclass
class SourceMapping:
    """One decoded mapping entry (all coordinates zero-based, CDP-style).

    ``source_index`` / ``original_line`` / ``original_column`` / ``name_index``
    are ``-1`` when the segment carried only a generated column (a mapping with
    no source counterpart — e.g. a generated-code-only region).
    """

    generated_line: int
    generated_column: int
    source_index: int = -1
    original_line: int = -1
    original_column: int = -1
    name_index: int = -1


@dataclass
class ParsedSourceMap:
    """A fully decoded source map.

    ``mappings`` is ordered by ``(generated_line, generated_column)`` exactly as
    it appears in the map, which is the order :meth:`SourceMapManager.lookup`
    relies on for its per-line scan.
    """

    version: int
    sources: list[str]
    sources_content: list[str | None]
    names: list[str]
    mappings: list[SourceMapping] = field(default_factory=list["SourceMapping"])
    file: str = ""
    source_root: str = ""

    def metadata(self) -> dict[str, Any]:
        """Compact view for ``/sourcemap/get`` (omits the heavy sources_content)."""
        return {
            "version": self.version,
            "file": self.file,
            "source_root": self.source_root,
            "sources": self.sources,
            "names_count": len(self.names),
            "mapping_count": len(self.mappings),
            "has_sources_content": any(c is not None for c in self.sources_content),
        }


def parse_source_map(raw: dict[str, Any]) -> ParsedSourceMap:
    """Parse a source map JSON dict (Source Map Revision 3) into structure.

    Decodes the ``mappings`` VLQ string: semicolons separate generated lines,
    commas separate segments within a line, and each segment's fields are
    *relative* to the previous segment (the standard delta encoding). The four
    delta accumulators (source index, original line/column, name index) persist
    across lines per the spec; only the generated column resets per line.
    """
    sources_raw: Any = raw.get("sources") or []
    sources: list[str] = [str(s) for s in sources_raw]
    contents_raw: Any = raw.get("sourcesContent") or []
    sources_content: list[str | None] = [
        (str(c) if c is not None else None) for c in contents_raw
    ]
    names_raw: Any = raw.get("names") or []
    names: list[str] = [str(n) for n in names_raw]

    mappings = _decode_mappings(str(raw.get("mappings", "") or ""))

    return ParsedSourceMap(
        version=int(raw.get("version", 3) or 3),
        sources=sources,
        sources_content=sources_content,
        names=names,
        mappings=mappings,
        file=str(raw.get("file", "") or ""),
        source_root=str(raw.get("sourceRoot", "") or ""),
    )


def _decode_mappings(mappings: str) -> list[SourceMapping]:
    """Decode the ``mappings`` string into ordered :class:`SourceMapping` rows."""
    result: list[SourceMapping] = []
    # Persistent delta accumulators (spec: these carry across line boundaries;
    # only generated_column resets each line).
    source_index = 0
    original_line = 0
    original_column = 0
    name_index = 0

    for generated_line, line_segment in enumerate(mappings.split(";")):
        generated_column = 0
        if not line_segment:
            continue
        for segment in line_segment.split(","):
            if not segment:
                continue
            fields = decode_vlq(segment)
            if not fields:
                continue
            generated_column += fields[0]
            entry = SourceMapping(
                generated_line=generated_line,
                generated_column=generated_column,
            )
            # A 4- or 5-field segment also carries a source position.
            if len(fields) >= 4:
                source_index += fields[1]
                original_line += fields[2]
                original_column += fields[3]
                entry.source_index = source_index
                entry.original_line = original_line
                entry.original_column = original_column
                if len(fields) >= 5:
                    name_index += fields[4]
                    entry.name_index = name_index
            result.append(entry)
    return result


def _decode_data_uri(uri: str) -> str:
    """Decode an inline ``data:`` source-map URI into its JSON text.

    Handles both base64 (``;base64,``) and percent/plain payloads. Raises a
    structured error if the URI is malformed so the caller surfaces a clean
    ``sourcemap_*`` envelope rather than a bare ``ValueError``.
    """
    try:
        header, encoded = uri.split(",", 1)
    except ValueError as exc:
        raise AgentBrowserError(
            error="sourcemap_decode_failed",
            hint="sourceMapURL is a data: URI with no comma separator",
            action="the inline source map is malformed",
        ) from exc
    if ";base64" in header:
        try:
            return base64.b64decode(encoded).decode("utf-8")
        except Exception as exc:
            raise AgentBrowserError(
                error="sourcemap_decode_failed",
                hint="failed to base64-decode the inline source map",
                action="the inline source map is malformed",
            ) from exc
    # Non-base64 data URIs carry the JSON directly (optionally percent-encoded);
    # urllib.parse.unquote is a no-op when there's nothing to unescape.
    from urllib.parse import unquote

    return unquote(encoded)


def _resolve_url(base_url: str, source_map_url: str) -> str:
    """Resolve a possibly-relative ``sourceMapURL`` against the script's URL."""
    return urljoin(base_url, source_map_url)


class SourceMapManager:
    """Discover, download, parse, and query source maps for parsed scripts."""

    def __init__(self, ctx: BrowserContextBase) -> None:
        self._ctx = ctx
        # scriptId -> parsed map. Memoises the download+parse so repeated reads
        # against one script are cheap.
        self._cache: dict[str, ParsedSourceMap] = {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_maps(self) -> list[dict[str, str]]:
        """List parsed scripts that declared a ``sourceMapURL``.

        Reads the debugger's live script inventory. Returns an empty list when
        the debugger was never enabled (no scripts seen yet) so the route can
        answer without forcing ``Debugger.enable`` on a session that isn't
        debugging — the agent enables the debugger first, navigates, then lists.
        """
        debugger = self._ctx._debugger_mgr
        if debugger is None or not debugger.is_enabled:
            return []
        return [
            {
                "script_id": s.script_id,
                "url": s.url,
                "source_map_url": s.source_map_url,
            }
            for s in debugger.list_scripts()
            if s.source_map_url
        ]

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    async def get_map(self, script_id: str) -> ParsedSourceMap:
        """Download (or decode), parse, cache, and return a script's source map.

        Cache hit returns the memoised object. On a miss, the script's
        ``sourceMapURL`` is resolved: a ``data:`` URI is decoded inline, any
        other URL is fetched through the base ``fetch`` (carrying page cookies)
        and parsed. Raises a structured ``sourcemap_*`` error when the script is
        unknown, has no source map, or the payload won't parse.
        """
        if script_id in self._cache:
            return self._cache[script_id]

        debugger = self._ctx._debugger_mgr
        script = (
            debugger._scripts.get(script_id)
            if debugger is not None and debugger.is_enabled
            else None
        )
        if script is None:
            raise AgentBrowserError(
                error="script_not_found",
                hint=f"No parsed script with id {script_id!r}",
                action="enable the debugger, (re)load the page, then list scripts",
            )
        if not script.source_map_url:
            raise AgentBrowserError(
                error="no_source_map",
                hint=f"Script {script_id!r} declared no sourceMapURL",
                action="this script ships without a source map; nothing to parse",
            )

        raw_json = await self._load_raw(script.url, script.source_map_url)
        try:
            parsed = parse_source_map(json.loads(raw_json))
        except AgentBrowserError:
            raise
        except Exception as exc:
            raise AgentBrowserError(
                error="sourcemap_parse_failed",
                hint=f"Could not parse the source map for {script_id!r}: {exc}",
                action="verify the .map URL returns valid Source Map v3 JSON",
            ) from exc
        self._cache[script_id] = parsed
        return parsed

    async def _load_raw(self, script_url: str, source_map_url: str) -> str:
        """Fetch or inline-decode the raw source-map JSON text."""
        if source_map_url.startswith("data:"):
            return _decode_data_uri(source_map_url)
        map_url = _resolve_url(script_url, source_map_url)
        result = await self._ctx.fetch(map_url)
        body = str(result.get("body", "") or "")
        if not body:
            raise AgentBrowserError(
                error="sourcemap_fetch_failed",
                hint=f"Fetched {map_url} but got an empty body",
                action="the .map URL may be wrong or require auth; check the URL",
            )
        return body

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def lookup(self, script_id: str, line: int, column: int) -> dict[str, Any]:
        """Reverse-map a generated ``line``:``column`` to its original position.

        Scans the (ordered) mappings for the requested generated line and picks
        the segment with the greatest ``generated_column`` not past ``column`` —
        the standard "closest preceding mapping" rule, since a generated
        position falls under the last mapping at or before it. Returns the
        original source path, line, column, and symbol name (when the segment
        carried one). ``matched`` is ``False`` when no mapping covers the
        position (e.g. generated-only code with no source counterpart).
        """
        parsed = await self.get_map(script_id)
        best: SourceMapping | None = None
        for m in parsed.mappings:
            if m.generated_line != line:
                continue
            if m.generated_column > column:
                # Mappings are column-ordered within a line; nothing further on
                # this line can precede the target column.
                break
            best = m

        if best is None or best.source_index < 0:
            return {
                "matched": False,
                "source": "",
                "original_line": -1,
                "original_column": -1,
                "name": "",
            }

        source = (
            parsed.sources[best.source_index]
            if 0 <= best.source_index < len(parsed.sources)
            else ""
        )
        name = (
            parsed.names[best.name_index]
            if 0 <= best.name_index < len(parsed.names)
            else ""
        )
        return {
            "matched": True,
            "source": source,
            "original_line": best.original_line,
            "original_column": best.original_column,
            "name": name,
        }

    async def list_sources(self, script_id: str) -> list[str]:
        """List the original source file paths declared in a script's map."""
        parsed = await self.get_map(script_id)
        return list(parsed.sources)

    async def get_source_content(self, script_id: str, source_path: str) -> str | None:
        """Return the embedded original source text for ``source_path``.

        ``None`` when the map declares the source but ships no inline content
        for it (``sourcesContent`` is optional and often partial). Raises
        ``source_not_in_map`` when the path isn't one of the map's sources at
        all, so a typo is distinguishable from a content-less entry.
        """
        parsed = await self.get_map(script_id)
        try:
            idx = parsed.sources.index(source_path)
        except ValueError as exc:
            raise AgentBrowserError(
                error="source_not_in_map",
                hint=f"{source_path!r} is not a source in this map",
                action="call 'sourcemap sources' to list the valid source paths",
            ) from exc
        if idx < len(parsed.sources_content):
            return parsed.sources_content[idx]
        return None
