#!/usr/bin/env python3
"""Fail closed before publishing a version whose exact commit lacks successful CI."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def check_version(root: Path, tag: str) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    version = project["version"]
    if tag != f"v{version}":
        raise ValueError(
            f"release tag {tag!r} does not match project version {version}"
        )
    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    versions = [p["version"] for p in packages if p["name"] == project["name"]]
    if versions != [version]:
        raise ValueError("project and uv.lock versions do not match")
    changelog = (root / "CHANGELOG.md").read_text()
    if not re.search(
        rf"^## {re.escape(version)} \(\d{{4}}-\d{{2}}-\d{{2}}\)$", changelog, re.M
    ):
        raise ValueError(f"CHANGELOG.md has no dated release entry for {version}")
    return version


def check_ci_runs(runs: list[dict], sha: str) -> None:
    matching = [
        run
        for run in runs
        if run.get("head_sha") == sha
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
    ]
    if not matching:
        raise ValueError("no main-branch CI push run exists for the release commit")
    latest = max(
        matching, key=lambda run: (run["run_number"], run.get("run_attempt", 1))
    )
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError("latest CI run for the release commit has not passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        default=os.environ.get("RELEASE_TAG"),
        required=not os.environ.get("RELEASE_TAG"),
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = check_version(root, args.tag)
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    tagged = subprocess.check_output(
        ["git", "rev-parse", f"refs/tags/{args.tag}^{{commit}}"], cwd=root, text=True
    ).strip()
    if tagged != sha or (
        os.environ.get("GITHUB_SHA") and os.environ["GITHUB_SHA"] != sha
    ):
        raise ValueError(
            "checkout, release event and tag must identify the same commit"
        )
    repository = os.environ["GITHUB_REPOSITORY"]
    query = urlencode(
        {"head_sha": sha, "event": "push", "branch": "main", "per_page": 100}
    )
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = Request(
        f"{api}/repos/{repository}/actions/workflows/ci.yml/runs?{query}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=30) as response:
        check_ci_runs(json.load(response)["workflow_runs"], sha)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as handle:
            handle.write(f"commit={sha}\n")
    print(f"Release {version}: tag, metadata, changelog and CI match {sha}")


if __name__ == "__main__":
    main()
