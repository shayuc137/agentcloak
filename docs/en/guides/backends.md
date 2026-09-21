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

CloakBrowser and Playwright keep a per-tab persistent CDP channel for manager events (debugger pauses, WebSocket frames). `cdp send` uses a separate persistent channel, retaining settings such as viewport overrides across successful calls. A raw-call timeout or cancellation closes only that channel; reapply its CDP settings after recovery. Closing a tab/session cleans up its channels. RemoteBridge sends CDP through its existing extension connection. Domains are enabled lazily.

CloakBrowser suppresses live Runtime console events. Console capture uses the native CDP Console domain and adds `error`/`unhandledrejection` listeners for uncaught errors. These listeners preserve console methods and browser launch settings; their internal debug message can appear in DevTools, while agentcloak exposes it as a regular error entry.

## Workspace and verification boundaries

Both local backends run one browser process. `browser.isolation` defaults to `shared`; `workspace` creates one context per canonical workspace, with page sessions inside it. Git repositories and ordinary assistant directories are supported. See [configuration](../reference/config.md#workspace-isolation) for identity priority and saved-state limits. Extra contexts do not provide separate launch flags or proxies, and persistent-profile extensions may not run inside them.

| Capability | Playwright | CloakBrowser | RemoteBridge |
|---|---|---|---|
| DPR captures and session media emulation | Real-browser regression; pointer requires headed mode | Real-browser regression; pointer requires headed mode | DPR unverified; session media/pointer changes unsupported |
| Input, viewport, local snapshots and JavaScript errors | Real-browser regression | Real-browser regression | Real MV3 smoke covers navigation, snapshot, viewport, evaluate and screenshot; detailed input/error parity remains unverified |
| Scripts, interception and request hold/release | Real-browser regression | Real-browser regression | Full parity remains unverified |
| Workspace storage and normal daemon restart | Real-browser + CLI regression | Real-browser + CLI regression | Unsupported; workspace mode is rejected |
| Same-name sessions in distinct workspaces | Independent pages | Independent pages | Single owner; cross-workspace claim/relaunch is rejected |
| Remote deployment | Not applicable | Not applicable | Local Chromium MV3 → WebSocket → daemon tested; external Windows/network deployment remains unverified |

The CI browser job runs the local control suites, workspace persistence and CLI recovery tests, plus a real extension smoke test. That test copies the extension and narrows only its discovery ports to a private daemon. It does not mock Chrome/CDP or use an existing user profile.

Local Playwright and CloakBrowser backends support forced session recovery and exact page CDP endpoints; see [recovery and evidence](recovery.md). Popup-loop regression tests cover synthetic same-origin pages; this does not establish the cause of every application-specific renderer freeze.
