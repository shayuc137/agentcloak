# Getting Started

## Installation

Modern Ubuntu/Debian and many other Linux distros block bare `pip install`
outside a venv (PEP 668). Recommend `uv tool` or `pipx` — each makes an
isolated environment per CLI:

```bash
uv tool install agentcloak      # https://github.com/astral-sh/uv
# or:
pipx install agentcloak         # https://pipx.pypa.io/

agentcloak doctor --fix         # verify and fix the environment in one step
```

If you'd rather stay with `pip`, use a venv first:

```bash
python -m venv .venv && source .venv/bin/activate
pip install agentcloak
```

One install gets you: CLI (`agentcloak` and `cloak`), MCP server (`agentcloak-mcp`), CloakBrowser stealth backend, httpcloak TLS fingerprint proxy.

`doctor --fix` runs the in-process repairs it can (downloads the ~200 MB CloakBrowser binary, creates the data dir) and prints a one-liner shell command for anything that needs system-level intervention. Adding `--sudo` runs that command for you when sudo / root is available.

### Detect the host OS before suggesting commands

When an agent needs to give the user a platform-specific instruction:

```bash
python -c "import platform; print(platform.system())"   # Linux | Darwin | Windows
```

Or just run the doctor — it already produces tailored hints:

```bash
agentcloak doctor          # read-only — emits per-distro Xvfb suggestion, etc.
```

### Run without installing — uv / uvx

```bash
uvx agentcloak doctor --fix             # one-shot env check
uvx agentcloak browser navigate https://example.com   # one-shot navigate
```

For MCP, point your client config at `uvx`:

```json
{ "command": "uvx", "args": ["agentcloak-mcp"] }
```

### System Dependencies (headless Linux only)

CloakBrowser runs in headless mode by default — no system dependencies needed. If you switch to headed mode on a server without a display (`headless=false`), Xvfb is auto-started. The doctor prints the right install command per distro:

| Distro | Install |
|--------|---------|
| Debian / Ubuntu | `sudo apt-get install -y xvfb` |
| Fedora / RHEL | `sudo dnf install -y xorg-x11-server-Xvfb` |
| Arch | `sudo pacman -S xorg-server-xvfb` |
| Alpine | `sudo apk add xvfb` |

Desktop Linux, macOS, and Windows need no extra dependencies.

### Verify Setup

```bash
cloak doctor             # read-only diagnosis
cloak doctor --fix       # diagnose + auto-fix (prints sudo command)
cloak doctor --fix --sudo  # diagnose + auto-fix + execute system command
```

Checks: Python version, PATH, required packages, CloakBrowser binary, Playwright system libs (Linux), Xvfb (when relevant), data directory, daemon connectivity.

## Install the Skill to Your Agent Platform

Installing the CLI doesn't make agents read this skill — the `SKILL.md` + `references/` bundle has to live in the agent platform's skills directory. `cloak skill install` handles that:

```bash
cloak skill install                      # interactive — lists detected platforms, pick one or all
cloak skill install --platform claude    # Claude Code (~/.claude/skills/agentcloak/)
cloak skill install --platform all       # every detected platform
cloak skill install --path ./my/skills/agentcloak  # custom location (forks, network shares)
```

Platform aliases: `claude`, `codex` (global `~/.codex`), `codex-project` (`./.codex`), `cursor` (`./.cursor`), `opencode` (`./.opencode`), `all`.

**How it works:** the bundle is copied once to a canonical path (`~/.agentcloak/skills/agentcloak/`), and each platform directory is **symlinked** at it. Upgrading the CLI then only needs:

```bash
cloak skill update      # refresh the canonical copy; symlinks pick it up for free
cloak skill uninstall   # remove links from every known platform (--remove-canonical also deletes the source)
```

**Windows fallback:** when symlinks aren't permitted (Developer Mode off), the install falls back to a full copy and prints `(copy)` — re-run `cloak skill install` after each upgrade since copies don't auto-update.

## How It Works

```
You (CLI) ──HTTP──> Daemon (auto-starts) ──Playwright──> Browser
```

The daemon starts automatically on your first command. It manages browser instances, tracks state, and exposes all operations via HTTP API.

If the daemon fails to start, the agent doesn't get a useful error directly — run `agentcloak doctor --fix` to find out *why*. The doctor works even when the daemon is down (it runs the checks in-process), so it's the right first step for any "daemon_unreachable" / "daemon_auto_start_failed" error.

## Configuration

Config file: `~/.agentcloak/config.toml`. Precedence: env vars > config.toml > defaults.

```toml
[daemon]
host = "127.0.0.1"        # daemon bind address
port = 18765               # daemon port (auto-increments if busy)
http_client_timeout = 120  # CLI/MCP → daemon request timeout (seconds)
auto_start_timeout = 15.0  # how long auto-start waits for /health
auto_start_poll_interval = 0.5

[browser]
default_tier = "auto"      # "auto" (CloakBrowser) | "cloak" | "playwright"
default_profile = ""       # auto-launch this profile
viewport_width = 1280
viewport_height = 720
navigation_timeout = 30    # seconds
action_timeout = 30000     # ms, per-action timeout
batch_settle_timeout = 5000 # ms, settle between batch actions
humanize = true            # CloakBrowser humanize layer — adds Bezier mouse curves,
                           # 70ms/char typing with 2% mistype simulation, and
                           # scroll smoothing. Anti-detection benefit is real but
                           # ``fill`` slows from ~30ms to multi-second per field
                           # because CloakBrowser intercepts it and replays as
                           # click + select-all + character-by-character type.
                           # Disable globally with AGENTCLOAK_HUMANIZE=false or
                           # ``cloak daemon start --no-humanize`` when bulk form
                           # fill speed matters more than typing cadence.
headless = true            # headless mode (default); set false for max stealth
idle_timeout_min = 0       # auto-shutdown after idle (0 = disabled)
stop_on_exit = false       # stop daemon when CLI exits
log_level = "warning"      # debug | info | warning | error
log_to_file = false        # write daemon log to ~/.agentcloak/logs/daemon.log
log_max_bytes = 10000000   # rotate when log exceeds this size (10 MB)
log_backup_count = 3       # keep N rotated logs
max_return_size = 50000    # /evaluate response cap (bytes)
screenshot_format = "jpeg" # jpeg (small) or png (lossless UI acceptance)
screenshot_quality = 80    # CLI JPEG quality
mcp_screenshot_quality = 50 # MCP JPEG quality (smaller base64)
proxy = ""                  # upstream browser proxy (e.g. "socks5://user:pw@host:1080")
dns_over_https = false      # false → append --disable-features=DnsOverHttps
extra_args = []             # extra Chromium flags, e.g. ["--lang=ja-JP"]

[security]
domain_whitelist = []       # glob patterns, e.g. ["*.github.com", "example.com"]
domain_blacklist = []       # blocked domains
content_scan = false        # scan page content against patterns
content_scan_patterns = []  # regex patterns for content scanning
```

### Environment Variables

All settings can be overridden with `AGENTCLOAK_` prefix:

| Variable | Example |
|----------|---------|
| `AGENTCLOAK_HOST` | `0.0.0.0` |
| `AGENTCLOAK_PORT` | `9000` |
| `AGENTCLOAK_DEFAULT_TIER` | `playwright` |
| `AGENTCLOAK_DEFAULT_PROFILE` | `my-session` |
| `AGENTCLOAK_VIEWPORT_WIDTH` | `1920` |
| `AGENTCLOAK_VIEWPORT_HEIGHT` | `1080` |
| `AGENTCLOAK_NAVIGATION_TIMEOUT` | `60` |
| `AGENTCLOAK_ACTION_TIMEOUT` | `60000` |
| `AGENTCLOAK_BATCH_SETTLE_TIMEOUT` | `1000` |
| `AGENTCLOAK_HUMANIZE` | `true` |
| `AGENTCLOAK_HEADLESS` | `false` |
| `AGENTCLOAK_IDLE_TIMEOUT_MIN` | `30` |
| `AGENTCLOAK_STOP_ON_EXIT` | `true` |
| `AGENTCLOAK_LOG_LEVEL` | `debug` |
| `AGENTCLOAK_LOG_TO_FILE` | `true` |
| `AGENTCLOAK_HTTP_CLIENT_TIMEOUT` | `180` |
| `AGENTCLOAK_AUTO_START_TIMEOUT` | `30` |
| `AGENTCLOAK_MAX_RETURN_SIZE` | `100000` |
| `AGENTCLOAK_SCREENSHOT_QUALITY` | `90` |
| `AGENTCLOAK_MCP_SCREENSHOT_QUALITY` | `40` |
| `AGENTCLOAK_DOMAIN_WHITELIST` | `*.github.com,example.com` |
| `AGENTCLOAK_DOMAIN_BLACKLIST` | `evil.com` |
| `AGENTCLOAK_CONTENT_SCAN` | `true` |
| `AGENTCLOAK_CONTENT_SCAN_PATTERNS` | `password=.*,ssn:\d+` |
| `AGENTCLOAK_PROXY` | `socks5://user:pass@host:1080` (browser only — fetch always uses local httpcloak) |
| `AGENTCLOAK_DNS_OVER_HTTPS` | `false` (default — turn on with `true`) |
| `AGENTCLOAK_EXTRA_ARGS` | `--lang=ja-JP,--disable-blink-features=AutomationControlled` (comma-separated) |
| `AGENTCLOAK_SKIP_FIRST_RUN_BANNER` | `1` (silence the first-run nudge) |

### Editing config from the CLI

```bash
cloak config set browser.proxy "socks5://host:1080"     # write one key
cloak config set browser.headless false browser.humanize true   # batch
cloak config add browser.extra_args "--lang=ja-JP"      # append to list
cloak config remove browser.extra_args "--lang=ja-JP"   # remove from list
cloak config unset browser.proxy                        # back to default
cloak config get browser.proxy                          # read one key
cloak config keys                                       # list all keys
```

Writes only touch `~/.agentcloak/config.toml`; env vars and built-in
defaults are never modified. Daemon must be restarted for `[browser]` /
`[daemon]` changes to take effect — the command prints the restart hint
when applicable.

## Daemon Management

The daemon auto-starts and auto-stops. Manual control:

| Command | Purpose |
|---------|---------|
| `cloak daemon start -b` | Start daemon in background |
| `cloak daemon stop` | Stop daemon |
| `cloak daemon status` | Check daemon status |

Default port: 18765. The daemon auto-increments if the port is busy (18765 → 18766 → ...).

## Chrome Extension (RemoteBridge)

To operate your real Chrome browser on another machine, install the extension:

```bash
cloak bridge extension-path
# Copy the output directory to your Chrome machine
# Chrome → chrome://extensions → Developer mode → Load unpacked
```

See `references/remote-bridge.md` for the full RemoteBridge guide.

## MCP Mode

agentcloak also works as an MCP server for non-CLI clients:

```bash
agentcloak-mcp
```

For Claude Code, add it with:

```bash
claude mcp add agentcloak -- agentcloak-mcp
```

The CLI + Skill mode is recommended for Claude Code (~300 tokens context vs ~6,000 for MCP).

## Troubleshooting

If something doesn't work, your first move is almost always:

```bash
agentcloak doctor --fix
```

The doctor knows about per-distro Xvfb packages, Playwright system libs, the CloakBrowser binary, PATH issues, and the daemon's liveness. It works in-process so it doesn't need the daemon to be running.

For the full error-code → recovery table (`daemon_unreachable`, `stealth_not_installed`, `xvfb_not_found`, `daemon_timeout`, and the rest), see `references/troubleshooting.md`.
