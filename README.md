<div align="center">

# agentcloak

Agent-native stealth browser -- see, interact, and automate the web.

You need a browser. Your agents do too.

[![PyPI](https://img.shields.io/pypi/v/agentcloak?style=flat)](https://pypi.org/project/agentcloak/)
[![Python](https://img.shields.io/pypi/pyversions/agentcloak?style=flat)](https://pypi.org/project/agentcloak/)
[![License](https://img.shields.io/github/license/shayuc137/agentcloak?style=flat)](https://github.com/shayuc137/agentcloak/blob/main/LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/shayuc137/agentcloak?style=flat)](https://github.com/shayuc137/agentcloak/commits/main)

<!-- README-I18N:START -->
**English** | [中文](./README.zh.md)
<!-- README-I18N:END -->

</div>

## Highlights

- **Pages as structured text** -- every page becomes an accessibility tree with `[N]` indexed elements; agents interact by index, not fragile CSS selectors
- **CLI + Skill on-demand loading** -- agents call `cloak` via Bash; the Skill lazy-loads at ~300 tokens (vs ~6,000 for MCP tool definitions)
- **CloakBrowser built-in stealth** -- 57 C++ patches on Chromium, passes common fingerprint checks and JS challenges
- **Session reuse** -- save/restore login profiles + RemoteBridge to operate your real Chrome browser
- **Network config** -- proxy (SOCKS5/HTTP), DNS-over-HTTPS control, and custom Chromium args via `cloak config set`
- **Daemon architecture** -- auto-starts on first command, manages browser lifecycle with a monotonic seq counter
- **Spells + API capture** -- wrap common site operations as one-liners; capture traffic, analyze patterns, generate spells automatically
- **Web reverse engineering** -- CDP-native debugger, network route interception, WebSocket/SSE capture, init-script hooks, and source-map decode -- one tool covers 90%+ of browser RE
- **MCP server with 38 tools** -- full compatibility with MCP-native clients (Claude Code, Codex, Cursor, etc.)

## Installation

**Requires Python 3.12+**

> [!TIP]
> **Agent-assisted install** -- copy this to your AI coding agent:
>
> ```text
> Install and configure agentcloak following this guide:
> https://github.com/shayuc137/agentcloak/blob/main/docs/en/getting-started/installation.md
> ```

<details>
<summary>Manual installation</summary>

```bash
# Recommended -- isolated environment, no PEP 668 hassle
uv tool install agentcloak     # or: pipx install agentcloak

cloak skill install            # installs the Skill bundle to your agent platform
cloak doctor --fix             # verify environment + download CloakBrowser
```

> **Why `uv tool` / `pipx`?** Modern Ubuntu/Debian and many other distros block `pip install` outside a venv (PEP 668 "externally-managed-environment"). `uv tool install` and `pipx install` each create a dedicated isolated environment for the package, so the install just works.

If you'd rather stick with `pip`, use a venv first:

```bash
python -m venv .venv && source .venv/bin/activate
pip install agentcloak
```

Everything is included: CLI (`agentcloak` and `cloak` shorthand), MCP server (`agentcloak-mcp`), CloakBrowser stealth backend, and httpcloak TLS fingerprint proxy. The patched Chromium binary (~200 MB) downloads automatically on first use to `~/.cloakbrowser/`.

**System dependencies (headless Linux only):**

CloakBrowser runs in headless mode by default (v0.2.0+) — no extra dependencies needed. If you opt into headed mode (`headless = false` for stronger anti-detection) on a server without a display, agentcloak auto-starts Xvfb:

```bash
sudo apt-get install -y xvfb
```

Desktop Linux, macOS, and Windows need no extra dependencies.

</details>

## Quick Start

The daemon starts automatically on the first command.

```bash
# Navigate and get the page snapshot in one call
cloak navigate "https://example.com" --snap
```

stdout is the answer itself — text-first, no JSON parsing required:

```text
https://example.com/ | Example Domain

# Example Domain | https://example.com/ | 8 nodes (1 interactive) | seq=1
  heading "Example Domain" level=1
  [1] link "Learn more" href="https://iana.org/domains/example"
```

```bash
# Interact using [N] refs (positional or --index N) -- --snap returns a fresh snapshot
cloak fill 3 "search query" --snap
cloak press Enter

# Take a screenshot (stdout = file path)
cloak screenshot
```

Errors go to stderr with a recovery hint and a non-zero exit code:

```text
Error: Element [99] not in selector_map (1 entries)
  -> run 'snapshot' to refresh the selector_map, or re-snapshot if the page changed
```

Need the legacy JSON envelope for scripts? Pass `--json` (or set `AGENTCLOAK_OUTPUT=json`):

```bash
cloak --json snapshot | jq -r '.data.tree_text'
```

The `--snap` flag on `navigate` and action commands keeps the observe-act loop tight -- no separate snapshot call needed between steps.

See the full [Quick Start tutorial](docs/en/getting-started/quickstart.md) for login persistence, profile management, and API capture.

## Usage Modes

| | Skill + CLI (recommended) | MCP Server |
|---|---|---|
| **How it works** | Skill auto-loads when browser is needed; agent calls `cloak` via Bash | `agentcloak-mcp` exposes 38 tools over stdio |
| **Context cost** | ~300 tokens (on-demand) | ~6,000 tokens (persistent) |
| **Best for** | Claude Code, any Bash-capable agent | MCP-native clients without Bash |

**Skill + CLI** -- three commands. The `cloak skill install` subcommand
copies the bundle from the wheel and symlinks every detected agent platform
to a single canonical source under `~/.agentcloak/skills/agentcloak/`:

```bash
# 1. Install agentcloak (CLI + daemon + stealth browser)
uv tool install agentcloak    # or: pipx install agentcloak (or pip install inside a venv)

# 2. Verify the environment (downloads CloakBrowser binary on first run)
cloak doctor --fix

# 3. Install the Skill bundle (interactive menu picks your agent platform)
cloak skill install
```

Non-interactive variants for scripted setup:

```bash
cloak skill install --platform claude         # ~/.claude/skills/
cloak skill install --platform codex          # ~/.codex/skills/
cloak skill install --platform all            # every detected platform
cloak skill install --path /custom/skills/dir # arbitrary location
```

| Agent platform | Skill location |
|---|---|
| Claude Code | `~/.claude/skills/agentcloak/` |
| Codex | `~/.codex/skills/agentcloak/` |
| Cursor | `.cursor/skills/agentcloak/` |
| OpenCode | `.opencode/skills/agentcloak/` |
| Other | Use `--path` to point at your agent's skill directory |

Run `cloak skill update` after upgrading agentcloak to refresh the bundle
(symlinked installs pick up changes automatically — copy-installs need a
re-run). `cloak skill uninstall` removes every link the installer created.

See the [Skill installation guide](docs/en/getting-started/installation.md#install-the-skill-bundle) for the offline curl+tar alternative and Windows notes.

**MCP Server** -- one-command setup for Claude Code:

```bash
claude mcp add agentcloak -- agentcloak-mcp
```

<details>
<summary>Other MCP clients (Codex, Cursor, uvx)</summary>

Add to `.codex/mcp.json` or `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "agentcloak": {
      "command": "agentcloak-mcp"
    }
  }
}
```

Or run without installing via `uvx`:

```json
{
  "mcpServers": {
    "agentcloak": {
      "command": "uvx",
      "args": ["agentcloak[mcp]"]
    }
  }
}
```

</details>

See the full [MCP setup guide](docs/en/guides/mcp-setup.md) for details.

## Browser Backends

| Backend | Stealth | Use case |
|---------|---------|----------|
| **CloakBrowser** (default) | 57 C++ patches, fingerprint / JS-challenge resistant | Most sites, anti-bot protected pages |
| **Playwright** | Standard Chromium | Development, testing, no stealth needed |
| **RemoteBridge** (experimental) | Real browser fingerprint | Operate your own Chrome on another machine |

See the [backends guide](docs/en/guides/backends.md) for configuration details and trade-offs.

## Configuration

```bash
cloak config list                                       # all settings with sources
cloak config set browser.proxy "socks5://host:1080"     # set a value
cloak config get browser.proxy                          # read a value
cloak config add browser.extra_args "--lang=ja-JP"      # append to a list
cloak config unset browser.proxy                        # reset to default
```

Key network settings:

```toml
# ~/.agentcloak/config.toml
[browser]
proxy = "socks5://user:pass@host:1080"   # upstream proxy (SOCKS5/HTTP)
dns_over_https = false                    # default: disabled, respects system DNS
extra_args = ["--disable-background-networking"]
```

All settings also accept environment variables (`AGENTCLOAK_PROXY`, `AGENTCLOAK_DNS_OVER_HTTPS`, `AGENTCLOAK_EXTRA_ARGS`). See the [config reference](docs/en/reference/config.md) for the full list.

## Architecture

```mermaid
graph TD
    subgraph Surface["Surface Layer"]
        Skill["Skill + CLI<br/>~300 tokens"]
        MCP["MCP Server<br/>38 tools"]
    end

    subgraph Engine["Engine"]
        Daemon["Daemon<br/>FastAPI + uvicorn + seq counter"]
    end

    subgraph Backends["Browser Backends"]
        Cloak["CloakBrowser<br/>stealth default"]
        PW["Playwright<br/>fallback"]
        Bridge["RemoteBridge<br/>real Chrome"]
    end

    Skill --> Daemon
    MCP --> Daemon
    Daemon --> Cloak
    Daemon --> PW
    Daemon --> Bridge
```

All backends extend a unified `BrowserContextBase` ABC. The base owns ~900 lines of shared behaviour (action dispatch, batch, dialog, self-healing); subclasses only implement 45 atomic operations. Layer isolation is enforced: CLI cannot import browser internals, daemon cannot import CLI, backends import neither.

See the [architecture docs](docs/en/explanation/architecture.md) for a deeper walkthrough.

## Documentation

| Topic | Link |
|-------|------|
| Installation | [docs/en/getting-started/installation.md](docs/en/getting-started/installation.md) |
| Quick Start tutorial | [docs/en/getting-started/quickstart.md](docs/en/getting-started/quickstart.md) |
| CLI reference | [docs/en/reference/cli.md](docs/en/reference/cli.md) |
| MCP tools reference | [docs/en/reference/mcp.md](docs/en/reference/mcp.md) |
| Configuration | [docs/en/reference/config.md](docs/en/reference/config.md) |
| Platform support | [docs/en/reference/platform-support.md](docs/en/reference/platform-support.md) |
| Browser backends | [docs/en/guides/backends.md](docs/en/guides/backends.md) |
| MCP setup | [docs/en/guides/mcp-setup.md](docs/en/guides/mcp-setup.md) |
| Architecture | [docs/en/explanation/architecture.md](docs/en/explanation/architecture.md) |

## Security

For vulnerability reports, see [SECURITY.md](SECURITY.md).

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, and PR guidelines.

## Acknowledgments

Built on [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) (stealth Chromium)
and [httpcloak](https://github.com/sardanioss/httpcloak) (TLS fingerprint proxy).

Design informed by
[bb-browser](https://github.com/epiral/bb-browser),
[browser-use](https://github.com/browser-use/browser-use),
[OpenCLI](https://github.com/jackwener/OpenCLI),
[GenericAgent](https://github.com/lsdefine/GenericAgent),
[pinchtab](https://github.com/pinchtab/pinchtab),
[open-codex-computer-use](https://github.com/iFurySt/open-codex-computer-use),
and [Scrapling](https://github.com/D4Vinci/Scrapling).

## License

[MIT](LICENSE)
