"""A green unrelated or older CI run must never authorize a release upload."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from urllib.error import URLError

import pytest

spec = importlib.util.spec_from_file_location(
    "release_gate", Path(__file__).resolve().parents[2] / "scripts/check_release.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def release_tree(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="example"\nversion="0.4.0"\n'
    )
    (tmp_path / "uv.lock").write_text('[[package]]\nname="example"\nversion="0.4.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "## Unreleased\n\n## 0.4.0 (2026-09-22)\n\nFeatures\n"
    )
    return tmp_path


@pytest.mark.parametrize("mismatch", ["tag", "lock", "notes"])
def test_release_version_disagreement_is_rejected(release_tree, mismatch):
    tag = "v0.4.0"
    if mismatch == "tag":
        tag = "v0.3.5"
    elif mismatch == "lock":
        (release_tree / "uv.lock").write_text(
            '[[package]]\nname="example"\nversion="0.3.5"\n'
        )
    else:
        (release_tree / "CHANGELOG.md").write_text("## Unreleased\n")
    with pytest.raises(ValueError):
        module.check_version(release_tree, tag)


def test_consistent_release_version_is_accepted(release_tree):
    assert module.check_version(release_tree, "v0.4.0") == "0.4.0"


def run(**overrides):
    return {
        "head_sha": "candidate",
        "event": "push",
        "head_branch": "main",
        "run_number": 3,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        **overrides,
    }


@pytest.mark.parametrize(
    "runs",
    [
        [],
        [run(head_sha="old")],
        [run(event="pull_request")],
        [run(head_branch="feature")],
        [run(conclusion="failure")],
        [run(status="in_progress", conclusion=None)],
        [run(), run(run_number=4, conclusion="cancelled")],
        [run(), run(run_attempt=2, status="queued", conclusion=None)],
    ],
)
def test_ci_gate_rejects_missing_or_unsuccessful_candidate(runs):
    with pytest.raises(ValueError):
        module.check_ci_runs(runs, "candidate")


def test_successful_candidate_ci_is_accepted():
    module.check_ci_runs([run(head_sha="old", run_number=5), run()], "candidate")


@pytest.mark.parametrize("failure", [None, "tag", "event", "api", "ci"])
def test_only_verified_checkout_is_exported(release_tree, monkeypatch, failure):
    monkeypatch.setattr(
        module, "__file__", str(release_tree / "scripts" / "check_release.py")
    )
    monkeypatch.setattr(sys, "argv", ["check_release.py", "--tag", "v0.4.0"])
    output = release_tree / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_SHA", "other" if failure == "event" else "candidate")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/browser")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.example.test")

    def revision(command, **kwargs):
        return "other" if failure == "tag" and command[-1] != "HEAD" else "candidate"

    monkeypatch.setattr(module.subprocess, "check_output", revision)

    def api(request, **kwargs):
        assert "head_sha=candidate" in request.full_url
        assert "branch=main" in request.full_url
        if failure == "api":
            raise URLError("unavailable")
        runs = [] if failure == "ci" else [run()]
        return io.BytesIO(json.dumps({"workflow_runs": runs}).encode())

    monkeypatch.setattr(module, "urlopen", api)
    if failure:
        with pytest.raises((ValueError, URLError)):
            module.main()
        assert not output.exists()
    else:
        module.main()
        assert output.read_text() == "commit=candidate\n"
