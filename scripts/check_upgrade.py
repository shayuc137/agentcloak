#!/usr/bin/env python3
"""Exercise an installed release-to-wheel upgrade without touching user state."""

from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile


class Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<!doctype html><title>Upgrade fixture</title><button>Ready</button>"
        )

    def log_message(self, *args):
        pass


def run(*args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180, **kwargs)
    if result.returncode:
        raise RuntimeError(
            f"{args[0]} exited {result.returncode}: {result.stdout}\n{result.stderr}"
        )
    return result.stdout


def request(port, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json", "X-Agentcloak-Session": "upgrade"},
    )
    with urlopen(req, timeout=15) as response:
        value = json.load(response)
    if not value.get("ok", False):
        raise RuntimeError(value)
    return value.get("data", value)


def ready(process, port, log):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(log.read_text())
        try:
            return request(port, "/health")
        except (URLError, TimeoutError):
            time.sleep(0.1)
    raise TimeoutError("upgrade daemon readiness")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--baseline", default="0.3.4")
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    state_files = [
        Path.home() / ".agentcloak" / name
        for name in ("daemon.json", "daemon.pid", "active-session.json")
    ]
    before = {p: p.read_bytes() if p.exists() else None for p in state_files}
    with tempfile.TemporaryDirectory(prefix="agentcloak-upgrade-") as directory:
        root = Path(directory)
        state = root / "state"
        state.mkdir()
        environment = {
            k: v for k, v in os.environ.items() if not k.startswith("AGENTCLOAK_")
        }
        environment.update(
            AGENTCLOAK_HOME=str(state), AGENTCLOAK_SKIP_FIRST_RUN_BANNER="1"
        )
        constraints = root / "constraints.txt"
        run(
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            str(constraints),
            cwd=Path(__file__).resolve().parents[1],
        )
        venv = root / "venv"
        run("uv", "venv", str(venv))
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run(
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--constraint",
            str(constraints),
            f"agentcloak=={args.baseline}",
        )
        # The baseline predates AGENTCLOAK_HOME. Only storage destinations are
        # redirected; commands, browser state and upgrade behavior stay real.
        launcher = root / "cli.py"
        launcher.write_text(
            "import os,sys\nfrom pathlib import Path\n"
            "import agentcloak.core.config as config\n"
            "root=Path(os.environ['AGENTCLOAK_HOME'])\n"
            "legacy=sys.argv.pop(1)=='legacy'\n"
            "if legacy: config._default_root=lambda:root\n"
            "else: assert config.load_config()[0].root==root\n"
            "from agentcloak.cli.commands import skill_cmd\n"
            "skill_cmd.CANONICAL=root/'skills'/'agentcloak'\n"
            "from agentcloak.cli.app import main\nmain()\n"
        )
        mode = "legacy"

        def cli(*arguments):
            return run(
                str(python), str(launcher), mode, *arguments, env=environment, cwd=root
            )

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        (state / "config.toml").write_text(
            f"[daemon]\nhost='127.0.0.1'\nport={port}\nidle_timeout_min=0\n"
            "[browser]\ndefault_tier='playwright'\nheadless=true\nhumanize=false\n"
        )
        cli("profile", "create", "upgrade")
        profile = state / "profiles" / "upgrade"
        overlay = profile / "config.toml"
        overlay.write_text("[browser]\nviewport_width=900\nviewport_height=600\n")
        target = root / "platform" / "agentcloak"
        cli("skill", "install", "--path", str(target))
        old_skill = (target / "SKILL.md").read_bytes()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/"

        @contextlib.contextmanager
        def daemon():
            log_path = root / f"{mode}-daemon.log"
            with log_path.open("wb") as log:
                process = subprocess.Popen(
                    [
                        str(python),
                        str(launcher),
                        mode,
                        "daemon",
                        "start",
                        "--profile",
                        "upgrade",
                    ],
                    env=environment,
                    cwd=root,
                    stdout=log,
                    stderr=log,
                )
                try:
                    health = ready(process, port, log_path)
                    yield health
                    request(port, "/shutdown", {})
                    process.wait(timeout=15)
                    assert process.returncode == 0
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()

        def evaluate(js):
            return request(port, "/evaluate", {"js": js})["result"]

        try:
            with daemon():
                request(port, "/navigate", {"url": url})
                evaluate(
                    "localStorage.setItem('login','saved');localStorage.setItem('removed','old');"
                    "document.cookie='upgrade_cookie=saved; Max-Age=86400; Path=/';true"
                )
                assert evaluate("localStorage.getItem('login')") == "saved"
            global_config = (state / "config.toml").read_bytes()
            profile_config = overlay.read_bytes()
            run(
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--constraint",
                str(constraints),
                "--reinstall-package",
                "agentcloak",
                str(wheel),
            )
            mode = "candidate"
            assert (target / "SKILL.md").read_bytes() == old_skill
            with daemon() as health:
                assert health["active_profile"] == "upgrade"
                request(port, "/navigate", {"url": url})
                assert evaluate("[innerWidth,innerHeight]") == [900, 600]
                assert evaluate("localStorage.getItem('login')") == "saved"
                assert "upgrade_cookie=saved" in evaluate("document.cookie")
                evaluate(
                    "localStorage.setItem('login','rotated');localStorage.removeItem('removed');true"
                )
                request(port, "/navigate", {"url": url})
                assert evaluate(
                    "[localStorage.getItem('login'),localStorage.getItem('removed')]"
                ) == ["rotated", None]
                assert request(port, "/snapshot")["mode"] == "compact"
            with daemon():
                request(port, "/navigate", {"url": url})
                assert evaluate(
                    "[localStorage.getItem('login'),localStorage.getItem('removed')]"
                ) == ["rotated", None]
            cli("skill", "update")
            if not target.is_symlink():
                cli("skill", "install", "--path", str(target))
            with ZipFile(wheel) as archive:
                prefix = "agentcloak/_skill_data/agentcloak/"
                files = [
                    name
                    for name in archive.namelist()
                    if name.startswith(prefix) and not name.endswith("/")
                ]
                assert files
                for name in files:
                    assert (
                        target / name.removeprefix(prefix)
                    ).read_bytes() == archive.read(name)
            assert (state / "config.toml").read_bytes() == global_config
            assert overlay.read_bytes() == profile_config
            assert not (state / "daemon.json").exists()
            print(
                json.dumps(
                    {
                        "baseline": args.baseline,
                        "candidate": health["version"],
                        "config_preserved": True,
                        "profile_storage_preserved": True,
                        "restart_persistence": True,
                        "skill_files": len(files),
                    }
                )
            )
        finally:
            server.shutdown()
            server.server_close()
    assert all(
        (p.read_bytes() if p.exists() else None) == content
        for p, content in before.items()
    )
    print("Host runtime records unchanged")


if __name__ == "__main__":
    main()
