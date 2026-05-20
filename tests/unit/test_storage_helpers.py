"""Unit tests for the storage JS-snippet builders (7a R4)."""

from __future__ import annotations

import pytest

from agentcloak.core.storage_helpers import (
    build_storage_clear_js,
    build_storage_delete_js,
    build_storage_get_js,
    build_storage_set_js,
    normalize_storage_type,
)


class TestNormalizeStorageType:
    def test_defaults_to_local(self) -> None:
        assert normalize_storage_type("") == "local"

    @pytest.mark.parametrize("value", ["local", "session", "LOCAL", "Session"])
    def test_accepts_known_areas(self, value: str) -> None:
        assert normalize_storage_type(value) in {"local", "session"}

    def test_rejects_unknown_area(self) -> None:
        with pytest.raises(ValueError, match="storage type"):
            normalize_storage_type("cookies")


class TestBuildGet:
    def test_single_key_uses_getitem(self) -> None:
        js = build_storage_get_js("local", "token")
        assert js == 'window.localStorage.getItem("token")'

    def test_full_dump_when_no_key(self) -> None:
        js = build_storage_get_js("session", None)
        assert "window.sessionStorage" in js
        assert "Object.fromEntries" in js

    def test_key_is_json_quoted_to_block_injection(self) -> None:
        # A key containing a quote must stay inside a JSON string literal, not
        # break out into the member expression.
        js = build_storage_get_js("local", 'a"); alert(1); //')
        assert 'getItem("a\\"); alert(1); //")' in js


class TestBuildSet:
    def test_set_quotes_key_and_value(self) -> None:
        js = build_storage_set_js("local", "k", "v")
        assert js == 'window.localStorage.setItem("k", "v")'

    def test_value_with_quotes_is_escaped(self) -> None:
        js = build_storage_set_js("local", "k", 'va"lue')
        assert 'setItem("k", "va\\"lue")' in js


class TestBuildDeleteClear:
    def test_delete_uses_removeitem(self) -> None:
        assert build_storage_delete_js("session", "k") == (
            'window.sessionStorage.removeItem("k")'
        )

    def test_clear_calls_clear(self) -> None:
        assert build_storage_clear_js("local") == "window.localStorage.clear()"

    def test_invalid_type_raises_in_builders(self) -> None:
        with pytest.raises(ValueError, match="storage type"):
            build_storage_clear_js("evil")
