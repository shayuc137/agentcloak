# Configuration reference

agentcloak works with zero configuration out of the box. All settings have sensible defaults and can be overridden via config file or environment variables.

## Precedence

Settings are resolved in this order (highest wins):

1. **Environment variables** (`AGENTCLOAK_*`)
2. **Config file** (`~/.agentcloak/config.toml`)
3. **Built-in defaults**

## Config file

Location: `~/.agentcloak/config.toml`

The file uses four sections: `[daemon]`, `[browser]`, `[security]`, and `[bridge]`.

```toml
[daemon]
host = "127.0.0.1"
port = 18765
http_client_timeout = 90
auto_start_timeout = 15.0
auto_start_poll_interval = 0.5
log_level = "warning"
log_to_file = false
log_max_bytes = 10000000
log_backup_count = 3

[browser]
default_tier = "auto"
default_profile = ""
viewport_width = 1280
viewport_height = 720
navigation_timeout = 30
idle_timeout_min = 30
stop_on_exit = false
headless = true
humanize = true
action_timeout = 30000
batch_settle_timeout = 2000
max_return_size = 50000
screenshot_quality = 80
mcp_screenshot_quality = 50
snapshot_max_nodes = 80
proxy = ""                  # e.g. "socks5://user:pass@host:1080"
dns_over_https = false      # false (default) appends --disable-features=DnsOverHttps
extra_args = []             # extra Chromium flags, e.g. ["--lang=ja-JP"]

[security]
domain_whitelist = []
domain_blacklist = []
content_scan = false
content_scan_patterns = []

[bridge]
# token is auto-generated on first daemon start; rotate via `cloak bridge token --reset`
local_idle_timeout = 1800
```

> [!NOTE]
> Invalid values (out-of-range port, unknown tier, bad log level) are caught at startup with a clear error message.

## Environment variables

All environment variables use the `AGENTCLOAK_` prefix.

### Daemon settings

| Variable | Config key | Default | Description |
|----------|-----------|---------|-------------|
| `AGENTCLOAK_HOST` | `daemon.host` | `127.0.0.1` | Daemon listen address |
| `AGENTCLOAK_PORT` | `daemon.port` | `18765` | Daemon listen port |
| `AGENTCLOAK_HTTP_CLIENT_TIMEOUT` | `daemon.http_client_timeout` | `90` | HTTP request timeout from CLI / MCP to daemon (seconds) |
| `AGENTCLOAK_AUTO_START_TIMEOUT` | `daemon.auto_start_timeout` | `15.0` | Seconds to wait for `/health` after auto-spawning the daemon |
| `AGENTCLOAK_AUTO_START_POLL_INTERVAL` | `daemon.auto_start_poll_interval` | `0.5` | Health-probe interval during daemon auto-start (seconds) |
| `AGENTCLOAK_LOG_LEVEL` | `daemon.log_level` | `warning` | Daemon log level (debug/info/warning/error) |
| `AGENTCLOAK_LOG_TO_FILE` | `daemon.log_to_file` | `false` | Mirror daemon logs to `~/.agentcloak/logs/daemon.log` with rotation |
| `AGENTCLOAK_LOG_MAX_BYTES` | `daemon.log_max_bytes` | `10000000` | Max bytes per rotated log file (10 MB default) |
| `AGENTCLOAK_LOG_BACKUP_COUNT` | `daemon.log_backup_count` | `3` | Number of rotated log files to retain |

### Browser settings

| Variable | Config key | Default | Description |
|----------|-----------|---------|-------------|
| `AGENTCLOAK_DEFAULT_TIER` | `browser.default_tier` | `auto` | Browser backend. `auto` resolves to `cloak` |
| `AGENTCLOAK_TIER` | (alias) | -- | Shorthand for `DEFAULT_TIER` |
| `AGENTCLOAK_DEFAULT_PROFILE` | `browser.default_profile` | `""` | Named profile to use on launch |
| `AGENTCLOAK_PROFILE` | (alias) | -- | Shorthand for `DEFAULT_PROFILE` |
| `AGENTCLOAK_VIEWPORT_WIDTH` | `browser.viewport_width` | `1280` | Browser viewport width in pixels |
| `AGENTCLOAK_VIEWPORT_HEIGHT` | `browser.viewport_height` | `720` | Browser viewport height in pixels |
| `AGENTCLOAK_NAVIGATION_TIMEOUT` | `browser.navigation_timeout` | `30` | Page load timeout in seconds |
| `AGENTCLOAK_NAVIGATION_TIMEOUT_SEC` | (alias) | -- | Alias for `NAVIGATION_TIMEOUT` |
| `AGENTCLOAK_IDLE_TIMEOUT_MIN` | `browser.idle_timeout_min` | `30` | Auto-shutdown after N minutes idle (0 = disabled) |
| `AGENTCLOAK_STOP_ON_EXIT` | `browser.stop_on_exit` | `false` | Stop daemon when CLI process exits |
| `AGENTCLOAK_HEADLESS` | `browser.headless` | `true` | Run browser without a visible window |
| `AGENTCLOAK_HUMANIZE` | `browser.humanize` | `true` | Enable CloakBrowser human-like behavior (mouse curves, typing cadence) |
| `AGENTCLOAK_ACTION_TIMEOUT` | `browser.action_timeout` | `30000` | Action timeout in milliseconds |
| `AGENTCLOAK_BATCH_SETTLE_TIMEOUT` | `browser.batch_settle_timeout` | `2000` | Time to wait between batch actions for page to settle (ms) |
| `AGENTCLOAK_MAX_RETURN_SIZE` | `browser.max_return_size` | `50000` | Max bytes returned from `/evaluate` before truncation (prevents MCP token blow-up) |
| `AGENTCLOAK_SCREENSHOT_QUALITY` | `browser.screenshot_quality` | `80` | Default JPEG quality for CLI screenshots (0-100) |
| `AGENTCLOAK_MCP_SCREENSHOT_QUALITY` | `browser.mcp_screenshot_quality` | `50` | Default JPEG quality for MCP screenshots (lower than CLI to save tokens) |
| `AGENTCLOAK_SNAPSHOT_MAX_NODES` | `browser.snapshot_max_nodes` | `80` | Default compact-mode snapshot node cap when caller didn't pass `--limit` / `max_nodes`. Pass `--limit 0` (CLI) or `max_nodes=0` (MCP) to opt back into the full tree. Only applied in compact mode. |
| `AGENTCLOAK_PROXY` | `browser.proxy` | `""` | Upstream proxy for the browser (e.g. `socks5://user:pass@host:1080`, `http://corp-proxy:3128`). Empty = direct. Restart the daemon for changes to take effect. |
| `AGENTCLOAK_DNS_OVER_HTTPS` | `browser.dns_over_https` | `false` | When `false` (default) agentcloak launches Chromium with `--disable-features=DnsOverHttps` so DNS goes through the system resolver — compatible with transparent / split-horizon proxies. Set to `true` to let Chromium use its bundled DoH resolvers. |
| `AGENTCLOAK_EXTRA_ARGS` | `browser.extra_args` | `[]` | Comma-separated extra Chromium command-line flags appended to every browser launch (e.g. `--lang=ja-JP,--disable-blink-features=AutomationControlled`). User-controlled escape hatch; agentcloak does not validate the contents. |

### Security settings

| Variable | Config key | Default | Description |
|----------|-----------|---------|-------------|
| `AGENTCLOAK_DOMAIN_WHITELIST` | `security.domain_whitelist` | `[]` | Comma-separated allow-list (glob patterns). When set, navigation to any non-listed domain is blocked with `domain_blocked`. Also enables Layer 3 untrusted-content wrapping for any snapshot from a non-whitelisted page already loaded. |
| `AGENTCLOAK_DOMAIN_BLACKLIST` | `security.domain_blacklist` | `[]` | Comma-separated block-list (glob patterns). Navigation to listed domains is blocked. Whitelist takes priority when both are set. |
| `AGENTCLOAK_CONTENT_SCAN` | `security.content_scan` | `false` | Enable regex content scanning. Matches surface as `security_warnings` in snapshot output (flag-only, not blocking). Action targets are also scanned and block on match. |
| `AGENTCLOAK_CONTENT_SCAN_PATTERNS` | `security.content_scan_patterns` | `[]` | Comma-separated regex patterns for content scanning (case-insensitive). |

### Bridge settings

| Variable | Config key | Default | Description |
|----------|-----------|---------|-------------|
| `AGENTCLOAK_BRIDGE_TOKEN` | `bridge.token` | auto-generated | Persistent auth token paired with the Chrome extension. Generated on first daemon start and written to `config.toml`; rotate with `agentcloak bridge token --reset`. |
| `AGENTCLOAK_LOCAL_IDLE_TIMEOUT` | `bridge.local_idle_timeout` | `1800` | Seconds the cached local browser stays warm after switching to `remote_bridge` tier. `0` means "never close". |

> [!NOTE]
> `file://`, `data:`, and `javascript:` URLs are always blocked regardless of whitelist/blacklist settings. See `docs/en/guides/security.md` for the full IDPI model.

## Browser tier resolution

The `default_tier` / `AGENTCLOAK_DEFAULT_TIER` value controls which browser backend is used:

| Value | Resolves to | Backend |
|-------|------------|---------|
| `auto` | `cloak` | CloakBrowser (default) |
| `cloak` | `cloak` | CloakBrowser stealth |
| `playwright` | `playwright` | Standard Playwright Chromium |
| `remote_bridge` | `remote_bridge` | RemoteBridge (real Chrome via extension) |

> v0.2.0 removed the legacy `patchright` alias — update older `config.toml`
> files to use `playwright` (or `cloak`) directly.

## Daemon CLI flags

The daemon can also be configured via CLI flags when starting manually:

```bash
cloak daemon start --host 0.0.0.0 --port 18765 --headed --profile my-session
```

| Flag | Description |
|------|-------------|
| `--host` | Listen address (overrides config) |
| `--port` | Listen port (overrides config) |
| `--headed` | Run browser in headed mode (visible window) |
| `--profile NAME` | Use a named browser profile |
| `--idle-timeout MINUTES` | Auto-shutdown after idle period |

## Filesystem paths

| Path | Purpose |
|------|---------|
| `~/.agentcloak/` | Root configuration directory |
| `~/.agentcloak/config.toml` | Configuration file |
| `~/.agentcloak/profiles/` | Saved browser profiles |
| `~/.agentcloak/logs/` | Daemon log files |
| `~/.agentcloak/active-session.json` | Current daemon session info |
| `~/.agentcloak/resume.json` | Session resume data |
| `~/.cloakbrowser/` | CloakBrowser binary cache |

## Example configurations

### Minimal stealth setup

```toml
[browser]
humanize = true
```

### Restrictive security

```toml
[security]
domain_whitelist = ["*.example.com", "api.service.io"]
domain_blacklist = ["*.tracking.com"]
content_scan = true
content_scan_patterns = ["password=\\w+", "api[_-]?key=\\w+"]
```

### Custom daemon port

```toml
[daemon]
host = "0.0.0.0"
port = 19000
```

Or via environment:

```bash
export AGENTCLOAK_HOST=0.0.0.0
export AGENTCLOAK_PORT=19000
```

### Browser network (proxy, DoH, extra flags)

```toml
[browser]
# Route every browser request through a residential SOCKS5 proxy
proxy = "socks5://user:pass@residential.example:1080"

# Keep system DNS (default). Set true to let Chromium use bundled DoH.
dns_over_https = false

# Extra Chromium flags. Useful for locale spoofing, feature flags, etc.
extra_args = ["--lang=ja-JP", "--disable-blink-features=AutomationControlled"]
```

Or via environment (restart the daemon afterwards):

```bash
export AGENTCLOAK_PROXY="socks5://host:1080"
export AGENTCLOAK_DNS_OVER_HTTPS=false
export AGENTCLOAK_EXTRA_ARGS="--lang=ja-JP,--disable-blink-features=AutomationControlled"
```

> [!NOTE]
> `proxy` only affects the browser. `cloak fetch` continues to use the
> built-in httpcloak local TLS proxy so its TLS fingerprint matches
> CloakBrowser — the two paths are intentionally independent.

## Configuring keys from the CLI

`cloak config` exposes git-style verbs for editing `~/.agentcloak/config.toml`:

```bash
cloak config                                  # list everything (with sources)
cloak config list                             # same as above
cloak config get browser.proxy                # print one value
cloak config set browser.proxy "socks5://host:1080"
cloak config set browser.headless false browser.humanize true   # batch set
cloak config add browser.extra_args "--lang=ja-JP"              # append to list
cloak config remove browser.extra_args "--lang=ja-JP"           # remove one entry
cloak config unset browser.proxy                                # restore default
cloak config keys                                               # list valid keys
```

Writes never touch env vars or defaults — only `~/.agentcloak/config.toml`.
Setting a key under `[daemon]` or `[browser]` requires a daemon restart;
the command prints `(restart daemon to apply)` when applicable.
