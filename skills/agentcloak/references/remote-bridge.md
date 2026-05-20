# RemoteBridge (Real Browser)

Operate your real Chrome browser on another machine via a Chrome extension + WebSocket connection.

## Setup

### 1. Install the Extension

```bash
cloak bridge extension-path
# outputs: /path/to/src/agentcloak/bridge/agentcloak-chrome-extension/
```

Copy that directory to your Chrome machine, then:
- Chrome → `chrome://extensions` → Enable Developer mode
- Click "Load unpacked" → select the extension directory
- The extension badge shows connection status (green = connected)

### 2. Start the Connection

**Direct mode** (extension connects to daemon directly):
```bash
cloak daemon start -b   # daemon on port 18765
# Extension auto-discovers daemon via port probing (18765-18774)
```

**Bridge mode** (for NAT/firewall traversal):
```bash
cloak bridge start -b   # bridge process relays between extension and daemon
```

### Token Authentication

Localhost connections are trusted automatically. For **remote** (non-localhost) connections the extension must present a bearer token:

```bash
cloak bridge token            # print the persistent token (stored in ~/.agentcloak/config.toml [bridge] token)
cloak bridge token --reset    # rotate it — already-paired extensions must be re-configured
```

Paste the token into the Chrome Extension's Options page to authorise its WebSocket connection. `--reset` severs any currently-connected extension on its next reconnect.

### Diagnose Connection Problems

```bash
cloak bridge doctor   # checks bridge config, extension files, and the WS toolchain (starlette/websockets/uvicorn)
```

Run this first when the extension can't connect — it reports the bridge port, candidate daemon list, whether the extension files are present, and whether the Python WS dependencies are installed.

## Usage

Once connected, all regular commands work on the real browser:

```bash
cloak snapshot           # sees the real page content
cloak click 5            # clicks in the real browser
cloak navigate "https://example.com"
```

### Privacy Note

In RemoteBridge mode `cloak tab list` returns **every open tab** in the
user's browser, not just the ones the agent claimed — personal mail,
banking, work tools, all of it. Those URLs and titles enter the agent's
context window as soon as they're read. Prefer `cloak bridge claim
--url-pattern ...` to scope what enters context, and avoid `tab list`
when the user hasn't already shown you what's open.

### Tab Claiming

Take over a tab the user already has open:

```bash
cloak bridge claim --url "github.com"            # claim tab matching URL
cloak snapshot                                   # now sees that tab
```

### Tab Group

Agent-managed tabs are automatically grouped under a blue "agentcloak" Chrome tab group, keeping them visually separate from user tabs.

### Session Finalize

When done, clean up with one of three modes:

```bash
cloak bridge finalize --mode close       # close all agent tabs
cloak bridge finalize --mode handoff     # keep tabs open for user to continue
cloak bridge finalize --mode deliverable # mark tabs as results for user to review
```

## CDP Coordination with jshookmcp

agentcloak and jshookmcp can share the same browser via CDP:

```bash
# 1. Start browser with agentcloak
cloak navigate "https://target-site.com"

# 2. Get CDP endpoint
cloak cdp endpoint
# prints: ws://127.0.0.1:18765/devtools/browser/...   (raw URL, pipe-friendly)

# 3. In jshookmcp: browser_attach(wsEndpoint)
# Now: navigation/interaction via agentcloak, JS analysis via jshookmcp
```

## Cookie Export

Export cookies from the real browser for use in scripts:

```bash
cloak cookies export                    # export all cookies as JSON
cloak cookies export --url "github.com" # export for specific domain
cloak cookies export --output cookies.json  # write to file instead of stdout

# Import back into a browser session (preserves httpOnly):
cloak cookies import -c '[{"name":"token","value":"abc","domain":".example.com","path":"/"}]'
```
