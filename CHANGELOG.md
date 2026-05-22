# Changelog

## 0.3.0 (2026-05-22)

Major release: web reverse engineering, multi-session, DX improvements, platform compatibility.

### Web Reverse Engineering (Phase 7b)

agentcloak now covers ~90% of web reverse engineering scenarios natively, replacing the need for jshookmcp in browser contexts.

- **Debugger** — breakpoints (URL regex + XHR pattern), single-step (over/into/out), call stack inspection, scope variable reading, paused-frame evaluation, source code search. Anti-debug bypass via `skip-pauses`
- **Source maps** — discover `.map` files from parsed scripts, pure-Python VLQ decode, reverse lookup (compiled line:col → original source:line:col), embedded source tree extraction
- **Streaming monitor** — WebSocket frame capture (sent/received) + Server-Sent Events monitoring, ring buffer with seq/since paging
- **Init script hooks** — inject JS before page scripts; 5 presets: `fetch`, `xhr`, `json_parse`, `crypto`, `timing` (log intercepted calls to console)
- **Network route interception** — abort/fulfill/continue with URL glob, resource type, and HTTP method filters
- **Header injection** — extra HTTP headers on every request (forged auth tokens)
- **GraphQL** — schema introspection + arbitrary query execution with browser cookies

### JS Profiling (Phase 7f)

- **Code coverage** — precise per-function coverage recording; find which JS ran during an operation
- **CPU profiling** — execution time distribution; locate hot functions (encryption/signing)
- **Heap snapshot** — V8 object graph dump; grep for keys/tokens/decrypted data
- **Performance metrics** — DOM node count, JS heap size, layout/recalc counts

### Multi-Session (Phase 7d)

- **SessionManager** — named sessions with independent browsers, per-session idle timeout (5min default), three-state lifecycle (registered/active/suspended)
- **Zero-config isolation** — `CLAUDE_CODE_SESSION_ID` auto-detected; each Claude Code instance gets its own browser without configuration
- **MCP session isolation** — `mcp-{pid}` auto-session with atexit cleanup
- **Session management** — `cloak session list` / `close`

### DX Improvements (Phase 6f)

- **Evaluate presets** — `--preset vue_inspect|react_inspect|jwt_decode|cookie_parse|storage_dump` for common reverse-engineering operations
- **Upload auto-find** — omit `--index` to auto-discover hidden `input[type=file]` elements (drag-drop uploaders); `--nth` selects which one
- **Download wait-click** — `cloak download wait-click --index N` atomic operation (arm waiter → click → await download in one request)
- **click --force** — skip pointer-event check for covered elements
- **Debugger URL search** — search across multiple scripts by URL pattern
- **Sourcemap 404** — clear HTTP status in error instead of cryptic parse failure
- **Auto WS/SSE monitor** — navigate auto-starts WebSocket/SSE listeners

### Platform Compatibility (Phase 6c)

- **Health metrics** — `/health` returns `uptime_seconds`, `request_count`, `active_connections` via ASGI middleware
- **CI matrix** — unit tests now run on ubuntu × windows × macos × Python 3.12/3.13/3.14
- **Platform support docs** — `docs/{en,zh}/reference/platform-support.md` with feature × platform matrix
- **Stale chromium detection** — `cloak doctor` warns about old CloakBrowser binaries (~700MB each) with cleanup command

### Daemon Reliability

- **Auto re-spawn** — daemon crash recovery with health probe confirmation
- **httpx retry** — `HTTPTransport(retries=2)` + split timeouts (connect=5s / read=90s)
- **Version consistency** — `/health` exposes version + route count; `doctor` warns on CLI/daemon mismatch; CLI 404 suggests daemon restart

### Bug Fixes

- `_cdp_send_impl` / `_cdp_enable_domain_impl` now wrapped with BackendError
- Console capture fallback via CDP `Runtime.consoleAPICalled`
- Config get fixed after nested config refactor (6d)
- Clipboard read fast-fail (5s) with clear headless limitation error
- SSRF guard uses explicit blocklist; unblocks `198.18.0.0/15` fake-IP range
- Three dogfood UX fixes (config list keys, console timing, storage get)

### Stats

- Routes: 59 → 102 (+43)
- MCP tools: 29 → 38 (+9)
- CLI commands: 27 → 38 (+11)
- Unit tests: 701 → 927 (+226)
- Chrome extension: on-demand CDP domain enable for reverse engineering

## 0.2.4 (2026-05-20)

Windows compatibility fixes from seed-user testing.

### Bug Fixes

- **Windows headed mode** — skip Xvfb virtual framebuffer on Windows; was triggering misleading `xvfb_not_found` error when `headless=false`.
- **Session file permissions** — wrap `os.chmod` in `contextlib.suppress` for cross-platform consistency (no-op on Windows but now guarded).
- **Windows spell directory** — user spell directory now uses `%APPDATA%` on Windows instead of Unix `.config` path.

## 0.2.3 (2026-05-17)

Seed-user review round 2: bug fixes, security, snapshot optimization, network config.

### Bug Fixes

- **`wait --url` / `frame focus --url`** — three-way URL matching: substring (default), glob (when `*` in middle), explicit `glob:` prefix. `?` treated as literal (URL query param), not glob wildcard.
- **`frame focus` snapshot** — snapshot now correctly switches to the focused iframe's content (was always returning main page due to CDP session targeting bug).
- **`batch` JSON array** — accepts both JSONL and JSON array format; gives friendly error on parse failure instead of raw traceback.

### Security & DX

- **`cookies export`** — output now includes domain column (`domain | name=value`); `--url` filter exposed in CLI.
- **RemoteBridge privacy** — docs now warn that `tab list` exposes all browser tabs in agent context.
- **humanize/fill behavior** — documented that `fill` under `humanize=true` is ~33x slower (CloakBrowser intercepts); guidance to use `type` for anti-detection, `fill` for speed.

### Snapshot Optimization

- **Indent compression** — tree indent step reduced from 2 to 1 space (~50% indent token savings on deep pages).
- **Token estimate** — snapshot header now includes `~NK tok` estimate (chars/4, no tokenizer dependency).
- **Content dedup** — content mode deduplicates adjacent identical lines (fixes Wikipedia/HN repetition from parent-child a11y node overlap).

### Network Config

- **`browser.proxy`** — SOCKS5/HTTP upstream proxy for the browser (`AGENTCLOAK_PROXY` env var).
- **`browser.dns_over_https`** — defaults to `false`, disabling Chrome's built-in DoH to respect system DNS / split-horizon proxies.
- **`browser.extra_args`** — arbitrary Chromium launch args passthrough (`AGENTCLOAK_EXTRA_ARGS` env var, comma-separated).

### Config CLI Upgrade

Five-verb declarative config management:

```bash
cloak config set <key> <value...>    # set scalar or replace list
cloak config get <key>               # read value
cloak config unset <key>             # reset to default
cloak config add <key> <value...>    # append to list
cloak config remove <key> <value>    # remove from list
cloak config keys                    # list all settable keys
```

Batch set, type-aware schema, write-after-validate with rollback, restart hints for browser/daemon keys.

---

## 0.2.2 (2026-05-17)

Rapid fix for 24 issues from seed-user review (16/17 fixed, 94% rate).

### Fixed

- `click --snap` snapshot loss in headless mode (navigation timing race)
- `resume` tab count incorrect (only reported first tab)
- `doctor` daemon check changed from `[fail]` to `[info]`
- daemon auto-start log level downgraded from warning to silent
- `daemon status` command added (was `health`)
- `config` command now shows full merged config with sources
- content mode text concatenation (Chromium a11y tree limitation, documented)
- spell User-Agent unified to Chrome UA
- default snapshot limit set to 80 nodes
- `navigate --snap` includes header separator line
- `cloak version` subcommand added
- recipes.md `--target` parameter fixed to positional syntax
- SKILL.md `--target` reference corrected
- `--snap` / `--include-snapshot` naming unified
- SKILL.md headless/headed configuration documented
- troubleshooting.md rewritten from text-first perspective
- getting-started.md installation updated to uv/pipx first

---

## 0.2.1 (2026-05-16)

- Updated project description and metadata
- CI: PyPI trusted publisher workflow
- CI: added Python 3.14 to test matrix
- CI: migrated to uv for consistent dependency resolution
- Simplified skill install (removed claude-global alias)

---

## 0.2.0 (2026-05-16)

Major architecture upgrade: RemoteBridge production-ready, CLI output redesign, dynamic tier switching.

### Highlights

- **Text-first CLI output** — stdout is the answer itself, no `jq` needed. `--json` flag for backward compat.
- **Dynamic tier switching** — `cloak launch --tier remote_bridge` hot-switches to user's Chrome without restarting daemon.
- **RemoteBridge fully functional** — evaluate, snapshot, tabs, capture all work through Chrome Extension.
- **`cloak skill install`** — one-command skill installation with platform auto-detection.
- **Bridge token persistence** — configure once, reconnects across daemon restarts.

### CLI

- Text-first output: 5 output primitives (success/value/info/error/json_out), errors to stderr
- `--snap` combo flag on all actions (action + observe in one step)
- `--limit` replaces `--max-nodes`, default snapshot mode is `compact`
- `cloak skill install/update/uninstall` — manage skill files across agent platforms
- `cloak launch --tier X` — hot-switch browser context (cloak/playwright/remote_bridge)
- `cloak bridge token [--reset]` — view or rotate persistent bridge auth token
- 20 CLI command groups, 41 daemon routes

### RemoteBridge (Chrome Extension) — experimental

> Remote Bridge is experimental. Core functionality works but has limited real-world testing. Report issues on GitHub.

- evaluate rewritten with CDP `Runtime.evaluate` (async support, no CSP issues)
- `activeTabId` state — navigate creates new tab instead of hijacking user's active tab
- Tab group lifecycle: blue "agentcloak" (active), green "handing off..." (handoff), auto-ungroup on disconnect
- CDP Network capture (capture start/stop/export works in RemoteBridge mode)
- CDP event forwarding (dialog detection, navigation feedback)
- Extension renamed to `agentcloak-chrome-extension/` for clarity
- Badge states: green ON / yellow wait / red ERR / grey OFF
- Options page: actionable error hints + Test Connection button

### Daemon

- FastAPI Accept negotiation: `text/plain` (CLI) vs `application/json` (MCP)
- `POST /launch` endpoint for context hot-switch
- `POST /bridge/token/reset` for hot token rotation
- ContextManager handles browser lifecycle + idle timer
- `config.example.toml` auto-generated on startup
- MCP responses: `exclude_none` for token savings

### Security

- CSP strip rules now per-tab only (was global)
- Token comparison via `secrets.compare_digest` (constant-time)
- `/ext` mutual exclusion (replace-on-reconnect for MV3 service worker restarts)

### Breaking Changes

- CLI default output is now **plain text** (was JSON). Use `--json` or `AGENTCLOAK_OUTPUT=json` for old behavior.
- Snapshot default mode is now `compact` (was `accessible`).
- `--include-snapshot` renamed to `--snap`.
- `--max-nodes` renamed to `--limit` (old name still accepted as alias).
- Extension directory renamed from `extension/` to `agentcloak-chrome-extension/`.

---

## 0.1.0 (2026-05-12)

Initial release.

### CLI

- 45 commands across navigation, interaction, content, capture, profile, tab, adapter, and daemon management
- JSON output envelope with `ok`/`seq`/`data` on success, `error`/`hint`/`action` on failure
- Batch action execution via `--calls-file` with auto-abort on navigation
- Top-level shortcuts: `cloak open`, `cloak snapshot`, `cloak click`, etc.
- `cloak doctor` diagnostics self-check

### MCP Server

- 18 tools covering navigation, interaction, content, network, capture, and management
- Auto-start daemon on first MCP request
- `pip install agentcloak[mcp]` optional dependency

### Browser Backends

- **PatchrightContext** — default backend, Playwright API, mid-stealth
- **CloakContext** — CloakBrowser high-stealth with Xvfb + humanize behavioral layer
- **RemoteBridgeContext** — Chrome extension + WebSocket bridge for remote browser control

### Core Features

- Daemon architecture with auto-start, PID management, health checks
- Accessibility-tree snapshots with `[N]` element refs (accessible/compact/content/dom modes)
- Monotonic seq counter for state tracking
- Profile persistence (create/list/launch/delete)
- Multi-tab management (list/new/close/switch)
- Network capture with HAR 1.2 export, pattern analysis, adapter generation
- Site adapter framework (Strategy enum, pipeline DSL, function mode)
- HTTP fetch with browser cookie forwarding
- Cloudflare Turnstile bypass (screenX patch extension)
- IDPI security model (domain whitelist/blacklist, content scanning)
- mDNS auto-discovery (optional zeroconf)
- Resume snapshot for session recovery
