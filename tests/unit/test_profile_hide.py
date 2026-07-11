"""Profile-scoped hide.json persistence tests."""

from __future__ import annotations

import json

import pytest

from agentcloak.core.errors import ProfileError
from agentcloak.daemon.services import ProfileService


def test_hide_selectors_round_trip_and_deduplicate(tmp_path: object) -> None:
    from pathlib import Path

    root = Path(str(tmp_path))
    service = ProfileService(root)

    service.write_hide_selectors("dos", [".toolbar", " .toolbar ", "#dev"])

    assert service.read_hide_selectors("dos") == [".toolbar", "#dev"]
    payload = json.loads((root / "dos" / "hide.json").read_text())
    assert payload == {"selectors": [".toolbar", "#dev"]}


def test_missing_hide_file_returns_empty_list(tmp_path: object) -> None:
    from pathlib import Path

    assert ProfileService(Path(str(tmp_path))).read_hide_selectors("dos") == []


def test_invalid_hide_file_raises_structured_profile_error(tmp_path: object) -> None:
    from pathlib import Path

    root = Path(str(tmp_path))
    profile = root / "dos"
    profile.mkdir()
    (profile / "hide.json").write_text('{"selectors": [1]}', encoding="utf-8")

    with pytest.raises(ProfileError) as raised:
        ProfileService(root).read_hide_selectors("dos")

    assert raised.value.error == "profile_hide_invalid"
