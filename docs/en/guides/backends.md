# Browser backends

agentcloak supports three browser backends. Each extends the `BrowserContextBase` ABC, so all CLI commands and MCP tools work identically regardless of backend.

## Overview

| Backend | Stealth level | Browser | Use case |
|---------|--------------|---------|----------|
| **CloakBrowser** (default) | High | Patched Chromium (57 C++ patches) | Most sites, anti-bot bypass |
| **Playwright** | None | Stock Chromium | Simple automation, debugging |
| **RemoteBridge** | Real fingerprint | User's Chrome | Login sessions, extensions |

## CloakBrowser (default)

CloakBrowser ships a patched Chromium binary with 57 C++ modifications that defeat common fingerprinting and bot detection. It is the default backend -- no flags needed.

```bash
cloak navigate "https://example.com"
```

### What CloakBrowser patches

- Browser fingerprint randomization (canvas, WebGL, audio, fonts)
- `navigator.webdriver` flag removal at the C++ level
- Automation indicator suppression (`--enable-automation` removed)
- Platform spoofing (Linux servers report Windows fingerprints)
- Proxy authentication support (including SOCKS5)

### Humanize mode

CloakBrowser can simulate human-like behavior: Bezier curve mouse movements, realistic typing cadence with occasional typos, and smooth scrolling with acceleration.

Enable via config or environment:

```toml
# ~/.agentcloak/config.toml
[browser]
humanize = true
```

```bash
# or via environment variable
export AGENTCLOAK_HUMANIZE=true
```

### Headed vs headless

CloakBrowser runs in headed mode by default because anti-bot systems detect headless browsers. On servers without a display, agentcloak automatically starts Xvfb (a virtual framebuffer).

```bash
# Install Xvfb on Debian/Ubuntu
sudo apt-get install -y xvfb
```

On desktop environments (Linux with display, macOS, Windows), headed mode uses the real display.

### Binary management

CloakBrowser downloads its Chromium binary automatically on first use:

- **Size**: ~200 MB
- **Cache location**: `~/.cloakbrowser/`
- **Updates**: Background check every hour, auto-downloads new versions

Override the binary path with `CLOAKBROWSER_BINARY_PATH` if you need a custom Chromium build.

## Playwright (fallback)

Standard Playwright Chromium without stealth patches. Useful for sites that don't have bot detection, or for debugging automation logic.

```bash
export AGENTCLOAK_DEFAULT_TIER=playwright
cloak navigate "https://example.com"
```

> [!WARNING]
> Playwright Chromium has no stealth capabilities. Sites with bot detection will likely block it. Use CloakBrowser for production work.

Playwright requires a separate browser binary download:

```bash
python -m playwright install chromium
```

## RemoteBridge (real Chrome)

RemoteBridge connects to a real Chrome browser on another machine via a Chrome extension and WebSocket. The browser keeps its genuine fingerprint, login sessions, and extensions.

```bash
cloak launch --tier remote_bridge
cloak navigate "https://example.com"
```

### When to use RemoteBridge

- You need access to real login sessions (no cookie export needed)
- The site checks for genuine browser profiles built over time
- You want to use Chrome extensions during automation
- You need the actual fingerprint of a real user browser

### Setup

1. **Install the extension.** Load the unpacked extension from `src/agentcloak/bridge/agentcloak-chrome-extension/` in Chrome (`chrome://extensions` > Developer mode > Load unpacked).

2. **Configure the connection.** Click the extension icon and set the daemon host/port. The extension auto-connects.

3. **Start using it.**

```bash
cloak launch --tier remote_bridge
cloak navigate "https://example.com"
```

See the [Remote Bridge guide](./remote-bridge.md) for detailed setup instructions, multi-machine configuration, and troubleshooting.

### Tab management with RemoteBridge

RemoteBridge supports tab claiming and session lifecycle management:

```bash
# Claim an existing tab
cloak bridge claim --url-pattern "dashboard"

# End session: close agent tabs
cloak bridge finalize --mode close

# End session: leave tabs open for user
cloak bridge finalize --mode handoff
```

## Switching backends

### Via config file

```toml
# ~/.agentcloak/config.toml
[browser]
default_tier = "cloak"   # or "playwright", "remote_bridge"
```

### Via environment

```bash
export AGENTCLOAK_DEFAULT_TIER=cloak
```

### Via CLI flag

Hot-switch the active tier with `cloak launch`:

```bash
cloak launch --tier remote_bridge
cloak navigate "https://example.com"
```

### Tier resolution

The `auto` tier (default) resolves to `cloak`.

| Setting | Resolves to |
|---------|------------|
| `auto` | `cloak` |
| `cloak` | `cloak` |
| `playwright` | `playwright` |
| `remote_bridge` | `remote_bridge` |

> The legacy `patchright` tier value was removed in v0.2.0 — set
> `default_tier = "playwright"` (or `cloak`) in `config.toml` if you have an
> older config file lying around.

## Comparison

| Feature | CloakBrowser | Playwright | RemoteBridge |
|---------|-------------|------------|-------------|
| Stealth patches | 57 C++ patches | None | N/A (real browser) |
| Bot detection bypass | High | Low | Inherent |
| Cloudflare bypass | Built-in (screenX patch) | No | Inherent |
| Browser binary | Auto-download | Manual install | User's Chrome |
| Headed mode | Default (Xvfb auto) | Optional | Always |
| Humanize support | Yes | No | N/A |
| Profile persistence | Yes | Yes | Inherent |
| Proxy support | Full (incl. SOCKS5 auth) | Limited | N/A |
| Setup complexity | Zero | One command | Extension install |
| Reverse engineering | Full (debugger / route / streaming / sourcemap) | Full | Full |

## Reverse-engineering support

All three backends support the Phase 7b reverse-engineering capabilities — debugger, network route interception, WebSocket/SSE streaming, source maps, init-script injection, and GraphQL. The commands are identical regardless of backend; see the [CLI reference](../reference/cli.md#reverse-engineering).

These features ride a persistent CDP channel. CloakBrowser and Playwright now keep a **per-tab persistent `CDPSession`** alive for event streaming (debugger pauses, WebSocket frames), alongside the existing short-lived sessions used by one-shot calls like `cloak cdp endpoint`. RemoteBridge tunnels the same CDP commands over its WebSocket. Each CDP domain is enabled lazily on first use, so a session that never reverse-engineers keeps the stealth backend's hot path clean.
