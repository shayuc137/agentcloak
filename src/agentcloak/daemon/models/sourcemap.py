"""Pydantic models for source-map routes (7b T4).

The source-map surface closes the reverse-engineering loop on top of the
debugger: T3 records each script's ``sourceMapURL``; these routes download +
parse the map (pure-Python VLQ decode) so an agent can reverse-map a compiled
``line:column`` back to the original source file + position and read the
embedded original source text.

All five routes are read-only — parsing and lookups never mutate the page or
the debugger session. ``/sourcemap/get`` returns map *metadata* (sources list,
counts) rather than the full decoded mappings to keep the payload small; the
heavy per-position detail comes from ``/sourcemap/lookup`` on demand.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "SourceMapEntryModel",
    "SourceMapGetRequest",
    "SourceMapGetResponse",
    "SourceMapListResponse",
    "SourceMapLookupRequest",
    "SourceMapLookupResponse",
    "SourceMapSourceContentRequest",
    "SourceMapSourceContentResponse",
    "SourceMapSourcesRequest",
    "SourceMapSourcesResponse",
]


# --- List -------------------------------------------------------------------


class SourceMapEntryModel(BaseModel):
    """One script that declared a source map."""

    script_id: str = Field(description="CDP script id (use with the other routes).")
    url: str = Field(description="Script URL the map belongs to.")
    source_map_url: str = Field(description="Declared sourceMapURL (URL or data: URI).")


class SourceMapListResponse(BaseModel):
    """Scripts with a declared source map, mined from the debugger inventory."""

    maps: list[SourceMapEntryModel] = Field(
        description="Scripts carrying a sourceMapURL."
    )
    count: int = Field(description="Number of scripts with a source map.")


# --- Get --------------------------------------------------------------------


class SourceMapGetRequest(BaseModel):
    """Download + parse a script's source map by script id."""

    script_id: str = Field(description="Script id from '/sourcemap/list'.")


class SourceMapGetResponse(BaseModel):
    """Parsed source-map metadata (the heavy mappings stay server-side)."""

    version: int = Field(description="Source Map spec version (normally 3).")
    file: str = Field(description="Generated file name the map describes, if any.")
    source_root: str = Field(description="sourceRoot prefix for the sources, if any.")
    sources: list[str] = Field(description="Original source file paths.")
    names_count: int = Field(description="Number of symbol names in the map.")
    mapping_count: int = Field(description="Number of decoded mapping segments.")
    has_sources_content: bool = Field(
        description="Whether the map embeds any original source text."
    )


# --- Lookup -----------------------------------------------------------------


class SourceMapLookupRequest(BaseModel):
    """Reverse-map a generated position to its original source position."""

    script_id: str = Field(description="Script id from '/sourcemap/list'.")
    line: int = Field(description="Zero-based generated (compiled) line number.")
    column: int = Field(0, description="Zero-based generated column number.")


class SourceMapLookupResponse(BaseModel):
    """Original source position for a generated position."""

    matched: bool = Field(description="Whether a mapping covered the position.")
    source: str = Field(description="Original source file path (blank if unmatched).")
    original_line: int = Field(
        description="Zero-based original line (-1 if unmatched)."
    )
    original_column: int = Field(
        description="Zero-based original column (-1 if unmatched)."
    )
    name: str = Field(description="Original symbol name, if the segment carried one.")


# --- Sources ----------------------------------------------------------------


class SourceMapSourcesRequest(BaseModel):
    """List the original source files declared in a script's map."""

    script_id: str = Field(description="Script id from '/sourcemap/list'.")


class SourceMapSourcesResponse(BaseModel):
    """Original source file paths in a parsed map."""

    sources: list[str] = Field(description="Original source file paths.")
    count: int = Field(description="Number of source files.")


# --- Source content ---------------------------------------------------------


class SourceMapSourceContentRequest(BaseModel):
    """Fetch the embedded original source text for one source file."""

    script_id: str = Field(description="Script id from '/sourcemap/list'.")
    source_path: str = Field(description="One of the paths from '/sourcemap/sources'.")


class SourceMapSourceContentResponse(BaseModel):
    """Embedded original source text for a source file."""

    source_path: str = Field(description="The requested source path.")
    content: str | None = Field(
        None, description="Original source text, or null when the map omits it."
    )
    available: bool = Field(
        description="Whether the map embedded content for this source."
    )
