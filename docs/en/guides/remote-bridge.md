# Remote Bridge

Remote Bridge lets an agentcloak daemon drive a real Chrome browser running on another machine — for example, a Linux server's agent operating your Windows desktop's Chrome with all its logins, extensions, and the genuine fingerprint built up over months of normal use. No headless detection, no cookie shuttling.

## Architecture

```
┌──────────────────┐   HTTP    ┌──────────────────┐    WS     ┌─────────────────────┐
│  cloak CLI / MCP │ ────────► │  daemon (Linux)  │ ◄──────►  │  Chrome extension   │
│                  │           │  18765 + /ext WS │           │  (Windows / macOS)  │
└──────────────────┘           └──────────────────┘           └─────────────────────┘
```

The Chrome extension speaks CDP (via `chrome.debugger`) and tunnels every command from the daemon over WebSocket. The daemon's `RemoteBridgeAdapter` translates Playwright-style requests into raw CDP and ships them through the tunnel.

The extension connects directly to the daemon's `/ext` WebSocket — point it at the daemon's host:port (defaulting to the auto-probed `18765-18774` range). Running the daemon on an interface the extension's machine can reach is all that's required; there is no separate relay process.

## Setup

### 1. Install the extension

On the daemon machine:

```bash
cloak bridge extension-path
# /home/you/.local/lib/python3.13/site-packages/agentcloak/bridge/agentcloak-chrome-extension
```

Copy that directory to the machine where Chrome lives, then in Chrome:

1. Open `chrome://extensions`
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked** and pick the extension directory
4. The extension icon should appear in the toolbar with a red badge ("disconnected")

### 2. Connect the extension

Click the extension icon and fill in the daemon address. The extension probes ports `18765-18774` on the configured host and auto-connects to the first daemon that answers `/ext`. The badge turns green when connected.

For most home networks the simplest setup is:

- Linux daemon: `cloak daemon start -b --host 0.0.0.0` (bind on all interfaces so the LAN can reach it)
- Extension options: host = the Linux server IP, port = 18765

### 3. Use the bridge

Once the extension is green, all regular commands work — they just drive the real browser:

```bash
cloak launch --tier remote_bridge
cloak navigate "https://example.com"
cloak snapshot                                   # sees the real page
cloak click 5                                    # clicks in real Chrome
```

Make the bridge the default for the daemon:

```bash
export AGENTCLOAK_DEFAULT_TIER=remote_bridge
```

## Reverse engineering over the bridge

All Phase 7b reverse-engineering capabilities work through the bridge — debugger breakpoints, network route interception, WebSocket/SSE streaming, source maps, init-script injection, and GraphQL. The extension enables the CDP domains these features need (`Debugger`, `Fetch`, `Network`) **on demand** — the first time the agent uses a feature that needs them — rather than holding them open for every session. The commands are identical to the local backends; see the [reverse engineering section](../reference/cli.md#reverse-engineering) of the CLI reference.

## Privacy note

RemoteBridge gives the agent the same view of your browser that you have.
`cloak tab list` returns **every open tab** — including personal email,
banking, work tools — not just the ones the agent claimed. URLs and titles
enter the agent's context window the moment it inspects them, so anything
visible in the title bar is effectively shared with whatever model is
driving the session.

Practical guardrails:

- Close (or move to a separate Chrome profile) tabs you don't want the
  agent to see before you connect.
- Prefer `cloak bridge claim --url-pattern ...` to put one explicit tab
  under control, rather than letting the agent scan the full tab list.
- Keep an eye on the blue "agentcloak" tab group — anything outside it is
  still readable, but it's a visual reminder of what's nominally agent
  scope.

## Tab claiming

The bridge starts out with no managed tabs. Two ways to put a tab under agent control:

```bash
# new tab opened by the agent
cloak tab new --url "https://github.com"

# or hijack a tab the user already opened
cloak bridge claim --url-pattern "github.com"     # first tab whose URL contains "github.com"
cloak bridge claim --tab-id 1234                  # specific Chrome tab id
```

Claimed tabs are added to a blue Chrome tab group named **agentcloak** so the user can visually tell agent-controlled tabs apart from their own.

## Session finalize

When the agent is done, clean up with one of three modes:

```bash
cloak bridge finalize --mode close         # close every agent-managed tab
cloak bridge finalize --mode handoff       # ungroup tabs, leave them open
cloak bridge finalize --mode deliverable   # rename group to "agentcloak results" (green)
```

Pick the mode that matches your hand-off intent: `close` for fully autonomous runs, `handoff` for "continue manually here", `deliverable` to flag results the user should review.

## WebSocket authentication

The extension connects directly to the daemon's `/ext` WebSocket. That endpoint accepts a Bearer token (auto-generated per daemon, printed at startup, stored in the session file). The extension picks it up via the options UI and sends it in its `hello` message — the browser WebSocket API can't set request headers, so the token travels in-band.

- **Localhost connections** bypass auth (you already have local access)
- **Remote connections** must include the token in the `hello` message; mismatches are closed with code `4001`

Rotate with `cloak bridge token --reset` (hot-applies to a running daemon), or by restarting the daemon.

## mDNS auto-discovery (optional)

If you install the optional `zeroconf` extra (`pip install agentcloak[mdns]`), the daemon advertises itself on the local network as `_agentcloak._tcp.local`. The extension can list available daemons and pick one without manual IP entry.

The auth token is **never** broadcast over mDNS — clients still need to obtain it from the session file.

## Cookie export

Pull cookies from the real browser for use in scripts or to seed a profile:

```bash
cloak cookies export                   # all domains, JSON to stdout
cloak cookies export --url github.com  # just one domain
cloak cookies import < cookies.json    # load into the active context
```

This is the easiest way to graduate a manual login into a reusable profile — log in via your real Chrome, export, then import into a new agentcloak profile.

## Troubleshooting

Remote JavaScript evaluation errors preserve the thrown message and first useful
CDP source location. Diagnostics are capped at 400 characters; a bare `Uncaught`
should no longer hide null access or syntax failures.

```bash
cloak bridge doctor
```

This checks: extension reachable, WebSocket connected, daemon `/ext` endpoint live, last extension heartbeat timestamp.

| Symptom | First step |
|---------|-----------|
| Extension badge stays red | Confirm daemon `--host 0.0.0.0` and firewall allows the port |
| `bridge_disconnected` errors | Check `cloak bridge doctor`; reload the extension from `chrome://extensions` |
| Commands hang on `navigate` | Chrome may have a permission popup blocking — focus the Chrome window and dismiss it |
| Token mismatch on remote LAN | Re-read the token from `~/.agentcloak/session.json` and paste into extension options |
| Extension drops after Chrome restart | The extension uses `chrome.alarms` keepalive but Chrome sometimes suspends MV3 service workers — click the icon once to wake it |
