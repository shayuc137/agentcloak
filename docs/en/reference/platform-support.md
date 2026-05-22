# Platform Support

agentcloak is developed and tested primarily on **Linux**, with **macOS** and **Windows** supported on a best-effort basis. This page documents which features work on each platform, what degrades, and the known platform-specific limitations.

CI runs the unit-test suite on `ubuntu-latest`, `windows-latest`, and `macos-latest` (Python 3.12–3.14). Browser-integration tests run on Linux only — the matrix below reflects code-level platform handling plus the CloakBrowser binary availability published upstream.

## Feature matrix

| Feature | Linux | macOS | Windows | Notes |
|---------|-------|-------|---------|-------|
| **CloakBrowser** (default stealth backend) | Full | Full | Full | Binary auto-downloads per platform. Linux x86_64/arm64 & Windows x86_64 ship the full 57-patch build (Chromium 146); macOS arm64/x86_64 ship a 26-patch build (Chromium 145). macOS first launch needs a one-time Gatekeeper approval (right-click → Open). |
| **Playwright** (fallback backend) | Full | Full | Full | Standard Chromium, no stealth. On Linux run `playwright install-deps chromium` for the system libs (`doctor` checks this). |
| **RemoteBridge** (your real Chrome) | Full | Full | Full | The Chrome MV3 extension is platform-independent; it connects back to the daemon over WebSocket regardless of where the daemon runs. Common layout: daemon on Linux, real Chrome on Windows/macOS. |
| **Xvfb / headed mode** | Conditional | Native | Native | Xvfb is **Linux-only** and only needed to run *headed* on a box with no `$DISPLAY` (auto-spawned then). macOS/Windows have a native display server, so headed mode works without Xvfb. Headless mode needs no display anywhere. |
| **humanize** (behavioral layer) | Full | Full | Full | Provided by CloakBrowser; OS-independent. Most meaningful in headed mode (real input events). |
| **httpcloak** (TLS-matched fetch proxy) | Full | Full | Full | Pure-Python local proxy; no platform branch. Degrades gracefully to a plain fetch if the proxy can't start. |
| **PDF export** | Headless only | Headless only | Headless only | Chromium can only render PDF in headless mode — this is a **mode** limit, not an OS limit. Runs identically on all three platforms when `headless = true`. |
| **Clipboard read** | Headed / bridge | Headed / bridge | Headed / bridge | Chromium blocks clipboard *read* in headless mode for security. Available in headed mode and via RemoteBridge on every OS. Clipboard *write* works in all modes. |
| **Background daemon** | Full | Full | Full | `start_new_session` on POSIX; `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP` on Windows. Background daemons log to a rotating file (stderr isn't visible when detached). |
| **`doctor --fix` (auto-install)** | Full | Partial | Not supported | Auto-installs Python deps + the CloakBrowser binary everywhere. The system-package step (Playwright libs, Xvfb) is Linux-only and uses your distro's package manager; on Windows the sudo/auto-fix path is skipped and the command is only printed. |

**Legend:** *Full* = works as designed · *Conditional / Partial* = works with the noted caveat · *Headless/Headed only* = depends on run mode, not OS · *Not supported* = unavailable on that platform.

## Known platform-specific behavior

These are the points in the codebase that branch on the operating system, summarized so you know what to expect:

- **Process liveness check** — POSIX uses `os.kill(pid, 0)`; Windows uses `OpenProcess` via `ctypes`. Stale `daemon.pid` files are detected on both.
- **Signal handling** — `SIGINT`/`SIGTERM` are wired to graceful shutdown on POSIX. The Windows `ProactorEventLoop` doesn't support `add_signal_handler`, so the daemon relies on the `/shutdown` route and Ctrl-C there instead.
- **Privilege check** — `doctor --fix --execute` needs root (`os.geteuid() == 0`) or `sudo` on POSIX; this path doesn't run on Windows.
- **User spell directory** — `%APPDATA%\agentcloak\spells` on Windows, `~/.config/agentcloak/spells` elsewhere.
- **Xvfb auto-spawn** — only attempted on Linux, headed, with no `$DISPLAY`.
- **Playwright system libs / Xvfb checks** — `doctor` only probes these on Linux; on macOS/Windows they report "not required on this OS".
- **Stale Chromium detection** — `doctor` flags old CloakBrowser Chromium builds left behind by auto-update (~700MB each) and prints a `rm -rf` to reclaim them. This works on **all platforms** — the cache dir (`~/.cloakbrowser`, or `CLOAKBROWSER_CACHE_DIR`) is resolved with pure `Path` operations, no OS-specific code.
- **Session file permissions** — `chmod 0o600` is applied to `active-session.json` on POSIX (best-effort; silently skipped where unsupported).

## Recommendations

- **Linux**: the primary target. Run headless for servers/CI; install Xvfb only if you need headed mode without a display. Run `cloak doctor --fix` to pull system libs.
- **macOS**: works natively. Approve the CloakBrowser binary once via Gatekeeper. No Xvfb needed.
- **Windows**: CLI, daemon, and all three backends work. Prefer headless mode; use `/shutdown` (or `cloak daemon stop`) rather than relying on signal-based shutdown. A common and well-supported setup is running the daemon on Linux/WSL and driving your real Windows Chrome through RemoteBridge.

If you hit a platform-specific bug, please file an issue with your OS, Python version, and the output of `cloak doctor --detail`.
