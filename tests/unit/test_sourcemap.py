"""Unit tests for Phase 7b T4 — SourceMap VLQ decoder + parser + manager."""

from __future__ import annotations

import pytest

from agentcloak.browser.managers.sourcemap import (
    SourceMapping,
    decode_vlq,
    parse_source_map,
)
from agentcloak.core.errors import AgentBrowserError


class TestDecodeVlq:
    def test_single_zero(self) -> None:
        assert decode_vlq("A") == [0]

    def test_four_zeros(self) -> None:
        assert decode_vlq("AAAA") == [0, 0, 0, 0]

    def test_positive_one(self) -> None:
        assert decode_vlq("C") == [1]

    def test_negative_one(self) -> None:
        assert decode_vlq("D") == [-1]

    def test_aaca(self) -> None:
        assert decode_vlq("AACA") == [0, 0, 1, 0]

    def test_multi_digit_value(self) -> None:
        # 'gB' decodes to 16: g=32 (continuation), B=1 → (1<<5)|0 = 32, >>1 = 16
        result = decode_vlq("gB")
        assert result == [16]

    def test_invalid_character_raises(self) -> None:
        with pytest.raises(AgentBrowserError, match="Invalid base64-VLQ character"):
            decode_vlq("!")

    def test_empty_string(self) -> None:
        assert decode_vlq("") == []


class TestParseSourceMap:
    @pytest.fixture
    def minimal_map(self) -> dict:
        return {
            "version": 3,
            "sources": ["src/app.ts"],
            "sourcesContent": ["const x = 1;"],
            "names": ["x"],
            "mappings": "AAAA",
        }

    def test_minimal_parse(self, minimal_map: dict) -> None:
        parsed = parse_source_map(minimal_map)
        assert parsed.version == 3
        assert parsed.sources == ["src/app.ts"]
        assert parsed.sources_content == ["const x = 1;"]
        assert parsed.names == ["x"]
        assert len(parsed.mappings) == 1
        m = parsed.mappings[0]
        assert m.generated_line == 0
        assert m.generated_column == 0
        assert m.source_index == 0
        assert m.original_line == 0
        assert m.original_column == 0

    def test_empty_mappings(self) -> None:
        parsed = parse_source_map(
            {"version": 3, "sources": [], "names": [], "mappings": ""}
        )
        assert parsed.mappings == []

    def test_multiline_mappings(self) -> None:
        parsed = parse_source_map(
            {
                "version": 3,
                "sources": ["a.js"],
                "sourcesContent": [None],
                "names": [],
                "mappings": "AAAA;AACA",
            }
        )
        assert len(parsed.mappings) == 2
        assert parsed.mappings[0].generated_line == 0
        assert parsed.mappings[1].generated_line == 1

    def test_metadata_omits_content(self, minimal_map: dict) -> None:
        parsed = parse_source_map(minimal_map)
        meta = parsed.metadata()
        assert "sources" in meta
        assert "sources_content" not in meta
        assert meta["has_sources_content"] is True

    def test_delta_accumulation(self) -> None:
        # "AAAA,CACA" = [0,0,0,0], [1,0,1,0]
        # Second segment: gen_col=0+1=1, src_idx=0+0=0, orig_line=0+1=1, orig_col=0+0=0
        parsed = parse_source_map(
            {
                "version": 3,
                "sources": ["a.js"],
                "names": [],
                "mappings": "AAAA,CACA",
            }
        )
        assert len(parsed.mappings) == 2
        m1 = parsed.mappings[1]
        assert m1.generated_column == 1
        assert m1.original_line == 1


class TestSourceMapping:
    def test_default_no_source(self) -> None:
        m = SourceMapping(generated_line=0, generated_column=0)
        assert m.source_index == -1
        assert m.original_line == -1

    def test_with_source(self) -> None:
        m = SourceMapping(
            generated_line=5,
            generated_column=10,
            source_index=0,
            original_line=3,
            original_column=7,
        )
        assert m.source_index == 0
        assert m.original_line == 3
