# Changelog

## Unreleased

Workspace-aware sessions on one shared daemon, browser evidence you can verify, richer pointer/keyboard input, screen recording, and hardened daemon ownership and release gates.

### Features

- **Workspace-aware sessions** — every command resolves a workspace (`--workspace PATH` > `AGENTCLOAK_WORKSPACE` > `browser.workspace_roots` > Git repository, shared by its worktrees > plain working directory; Git is not required) and a session (`--session ID` > `AGENTCLOAK_SESSION` > worktree/root path hash). Each session owns its tabs, `[N]` refs, viewport, routes, scripts and console buffer, and sessions with the same ID in different workspaces never collide. `session list` shows readable labels, active actions and queue depth; `--all` spans workspaces
- **Configurable storage isolation** — `browser.isolation` defaults to `shared`, so sessions keep sharing the profile's login state. Set `workspace` (restart required) to give each workspace its own cookies/localStorage/IndexedDB, persisted under `~/.agentcloak/workspaces/` on close, idle reclamation or normal shutdown. This is storage isolation, not a separate browser process; RemoteBridge requires `shared`
- **Session recovery** — per-session request queues are bounded by `browser.action_timeout` (`session_busy` names the occupying route); a disconnected client cancels its request; `session close --force` cancels queued work, terminates a frozen page and closes only that session (RemoteBridge returns `force_recovery_unavailable`). Page or browser loss is reported as `page_recreated` / `page_lost` instead of silently continuing on a blank page
- **Verifiable captures** — screenshot JSON carries `url`, `title`, `viewport`, `dpr` and decoded `pixel_width`/`pixel_height`; `navigate --expect-path` and `screenshot --expect-url` fail with `url_mismatch` on silent redirects; `cdp endpoint --page` returns the session's exact page target for external CDP clients
- **Viewport and emulation** — `viewport set WxH [--dpr R]` resizes without navigating; `screenshot --viewport WxH --dpr R` applies temporary overrides restored after capture; `emulate` sets session color scheme and reduced motion (headed or headless), pointer `coarse`/`fine` on headed local browsers only, and `emulate reset` clears them. Local screenshots go through CDP to preserve device metrics
- **Screen recording** — `record start [--format webm|zip]`, `status`, `stop -o FILE` capture the tab active at start with bounded frames, seconds and bytes; WebM needs `ffmpeg` on the daemon host, ZIP ships JPEG frames plus a timestamped manifest. Local backends only
- **Annotated screenshots** — `screenshot --annotate` draws fresh `[N]` refs on the image and returns `annotations` with CSS-pixel `box` and `in_viewport`; `--within`, `--find` and `--limit` narrow the labels without cropping
- **Richer input** — `drag` between refs or coordinates with `--hold`, `--duration`, `--steps` and per-step `--sample JS`; `hover --at x,y` / `--offset dx,dy`; `--selector CSS` on `click`/`fill`/`hover` for known controls; `snapshot --find TEXT` keeps only matching subtrees with actionable refs; key names and `Ctrl`/`Cmd`/`Opt` aliases are case-insensitive; refs accept `12` or `[12]`
- **Network observation and control** — `network --pending [--filter GLOB]` lists in-flight requests including long-lived SSE; `route add --hold` pauses matching requests for loading-state evidence and `route release` resumes them; `route list` reports hit counts and warns on zero hits; `script list` shows whether each init script has been injected; console output accumulates across navigations with page URL and timestamp, cleared explicitly with `console clear`
- **Streaming JSONL batch** — `cloak batch` runs mixed daemon requests from a file or stdin through one process and connection pool, emitting one indexed envelope per record and stopping at the first failure. `do batch` remains the action-only runner with result references
- **Raw CDP** — `cdp send --timeout MS` bounds each call and exits nonzero on protocol errors; `--params-file PATH` (or `-` for stdin) reads large parameter objects without shell argument limits
- **Daemon ownership** — `AGENTCLOAK_HOME` relocates the whole state directory; the daemon holds `daemon.lock` for its lifetime so a duplicate start fails cleanly; clients discover the daemon through `/health` and never depend on PID visibility or a writable state directory. `daemon start --log-level` and `SIGUSR1` task dumps aid diagnosis; `cloak version --json`, `/health` and `daemon.json` expose a `build_id`
- **MCP** — four new tools (`agentcloak_viewport`, `agentcloak_emulate`, `agentcloak_cdp_send`, `agentcloak_record`, 39 → 43) and new parameters on `navigate`, `snapshot`, `screenshot`, `action`, `network`, `status`, `tab` and `route` matching the CLI additions above

### Bug Fixes

- **Structured CLI failures** — every failure exits nonzero; `--json` returns `ok:false` with `error.code` / `error.message`, and the stderr line is `Error [code]: message`. `js evaluate` throws and rejected promises now fail instead of printing an error string
- **Interception and hooks take effect** — `route add` and `script add` activate their CDP mechanisms on registration and expose hit/application status for verification
- **Console capture across navigation** — CloakBrowser suppresses live Runtime console events; capture now uses the native Console domain plus `error`/`unhandledrejection` listeners, so logs survive navigation
- **Snapshot coverage** — focusable custom elements (including `tabindex="-1"`) and exposed `button`/`menuitem` roles receive refs in both `compact` and `accessible` modes
- **Main-world evaluation bound to the owning document** — `js evaluate --world main` selects the active tab's main document by frame and unique execution-context identity; iframes can no longer redirect it and a context destroyed by navigation fails without replaying the script
- **localStorage preserved across navigation** — profile mode no longer replays a stale `localStorage-snapshot.json` over live values; `profile create --from-current` imports cookies and localStorage into the native profile once at creation
- **Pending requests follow their document** — replacing a document or detaching its frame retires its in-flight requests; history/hash updates keep them; EventSource and fetch-based SSE stay observable but never block batch settling. Only the next snapshot after new actions waits for finite requests; consecutive snapshots do not repeat the wait
- **Browser and tab lifecycle** — a closed page or disconnected local browser is rebuilt on the next request; `tab close --others` reports the closed IDs consistently across CLI, HTTP and MCP; popups are surfaced via `new_tab` with a `pending` marker while still loading
- **Keyboard cleanup** — invalid key combinations are rejected before input, and failed or cancelled presses release the modifiers they pressed
- **Network evidence** — `network --since last_action` includes the most recent action's own requests; route patterns treat `?` literally so `*/items?*` and `*/items/?*` differ
- **Storage on `about:blank`** — JavaScript storage access after `session close` returns an actionable `storage_origin_error` with the current URL instead of a raw `SecurityError`
- **mDNS advertisement** — registration runs asynchronously after HTTP readiness, announces the actual bound port (including port fallback), skips loopback-only listeners, and closes its resources on failure or shutdown without blocking startup
- **Validation errors stay serializable** even when validator context contains exceptions or non-finite values

### Documentation

- Configuration precedence now matches runtime behavior: environment variables override the global config file (`CLI args > profile config > env vars > global config > defaults`)
- New [recovery and evidence guide](docs/en/guides/recovery.md); backend capability matrix with explicit verified/unverified boundaries for RemoteBridge; workspace isolation reference; corrected headless-by-default and `discovery` extra descriptions

### Engineering

- **Browser regression in CI** — dual-backend control suites, workspace persistence, multi-session and CLI recovery tests, plus a real Chromium MV3 extension smoke test for RemoteBridge (local machine only; cross-machine deployment remains unverified)
- **Release gates** — PyPI publication requires a matching tag, project/lockfile version and dated CHANGELOG entry, and a successful main-branch CI run for the exact release commit; `scripts/check_upgrade.py` upgrades from the published `0.3.4` package and verifies configuration, profile cookies/localStorage, restart persistence and skill refresh
- Refresh locked dependencies: Playwright 1.63.0, uvicorn 0.53.0, Ruff 0.16.8, pyright 1.1.414, PyJWT 2.14.0, greenlet 3.5.6, idna 3.20

## 0.3.5 (2026-09-09)

Hardened profile subsystem, macOS platform fix, and a security-relevant dependency refresh.

### Bug Fixes

- **Profile stickiness hardening** — config overlay timing, health report accuracy, request-path wiring, and hide-source tracking all fixed in a four-commit series driven by real-world DOS feedback. Profiles now reliably pin sessions to the profile browser across all entry points.
- **macOS headed startup** — restrict Xvfb auto-start to Linux so macOS uses its native display server (#3).
- **Backend-aware doctor** — check the configured browser backend, honor CloakBrowser binary/cache overrides, and resolve Playwright's current system or managed Chromium build (including headless shell). RemoteBridge skips local browser requirements, and a working CloakBrowser installation passes without a system Chromium on PATH (#4).
- **Skill description overflow** — trim bundled skill description to the 1024-character MCP limit (#2, thanks @Blue-B).

### Dependencies and CI

- Refresh 29 locked packages, including CloakBrowser 0.5.10, Playwright 1.62.0, HTTPcloak 1.7.2, MCP 1.30.0, and Ruff 0.16.6. Keep the MCP SDK below 2 until its breaking server API migration is implemented.
- Require stable HTTPcloak 1.7.2+ and cryptography 50.0.1+; the latter includes the fix for CVE-2026-69247 and prevents an existing installation from retaining the vulnerable transitive version.
- Update checkout, setup-python, and setup-uv Actions. Run quality checks and unit tests against the committed lockfile, add dual-backend browser smoke tests, and audit both exported locked dependencies (including optional and development extras) and a fresh installation.

## 0.3.4 (2026-07-15)

Follow-up polish to the profile subsystem after real-world usage: SPAs that stash auth in localStorage now survive profile relaunch, per-profile config overrides land, and a couple of surprising cross-cutting bugs get fixed.

### Features

- **localStorage auto dump/restore in profile mode** — launching a profile now hydrates localStorage from the profile directory, and changes are dumped back on shutdown. SPAs that keep JWT/session tokens in `localStorage` (Supabase, Firebase, most modern auth flows) stay logged in across restarts without extra work; previously only cookies persisted, so token-in-localStorage apps redirected back to `/login`
- **`profile create --from-current` snapshots localStorage** — the snapshot taken from the live session now captures cookies **and** localStorage, so the seeded profile matches what the browser was actually holding
- **Per-profile `config.toml` overrides** — drop a `config.toml` inside `~/.agentcloak/profiles/<name>/` to override any subset of global config for that profile only (same schema as the global file). Useful for per-profile proxy, humanize, headless, or Chromium `extra_args`
- **`session close` default target** — omit `SESSION_ID` (`cloak session close`) to close the current session; previously an explicit ID was required, which was awkward from single-session shells

### Bug Fixes

- **Profile mode session routing** — every request in profile mode is now pinned to the profile browser instead of falling through to the auto-created session for the caller's `CLAUDE_CODE_SESSION_ID` / `mcp-{pid}`. Before, launching a profile then running a command from Claude Code would silently operate on the auto-session browser, so cookies/localStorage/tabs from the profile were invisible
- **`storage` on `about:blank`** — returns a structured `storage_origin_error` (with a hint to navigate somewhere first) instead of surfacing a raw browser `SecurityError`. Agents can now branch on the error kind rather than parse a JS stack trace

## 0.3.3 (2026-07-11)

Driven by real-world agent feedback from a frontend-acceptance project (DOS): overlay hiding, richer JS error diagnostics, login-state restore, and a batch of observe-act ergonomics.

### Features

- **Three-tier overlay hiding** — `cloak hide add/remove/list` manages persistent CSS hiding that removes elements from the a11y snapshot, screenshots, and click hit-testing in one mechanism. Sources compose: one-shot `--hide CSS` on snapshot/screenshot, profile-persisted `hide.json` (survives relaunch, inherited by per-session browsers), and the page-side `[data-cloak-hide]` opt-in attribute. `--keep-overlays` reveals everything for one observation. Solves dev-mode toolbars eating `[N]` refs, polluting screenshots, and intercepting clicks
- **JS evaluate diagnostics** — errors now return the exception message and source location (`TypeError: Cannot read properties of null ... at <anonymous>:1:54`) instead of a bare `Uncaught`; `js evaluate --file script.js` reads code from a file
- **`screenshot --wait-for CSS`** — wait for an element before capturing; times out with a structured error and produces no file
- **`cookies restore`** — `cookies export` drops a client-side snapshot beside the active profile (0600); `restore` imports it back in one command. Import now normalizes chrome.cookies / CDP / Playwright cookie shapes, fixing the bridge-export → local-import round trip (verified with a real 2773-cookie session)
- **Hash anchor navigation** — `navigate "url#id"` waits for late-rendered anchor targets in SPAs and scrolls to them; anchor misses are reported in text output. Param-style (`#token=...`) and hashbang fragments are skipped
- **Selector-scoped snapshots** — `snapshot --within CSS` scopes the tree and `[N]` refs to a subtree
- **Screenshot pixel diff** — `cloak diff screenshot BASELINE [--current FILE]` for visual-regression checks
- **Screenshot format config** — `browser.screenshot_format` config key, hot-reloaded, with extension-based inference on `--output`

### Bug Fixes

- **Silent force-click degradation** — `click --force` with a non-default `--button`/`--count` now returns a structured `invalid_argument` error instead of silently performing a single left click; RemoteBridge force-click returns `index`/`element` like the local backend
- **Controlled-input fill over RemoteBridge** — value setting goes through the native prototype setter so React/Vue controlled components register the change
- **Launch-path drift** — a `--headless` daemon on a display-less host no longer crashes launching per-session browsers: CLI overrides are written back into the shared config so all three launch paths agree
- **zeroconf 0.150 compatibility** — mDNS discovery listener updated for the stricter `ServiceListener` API

## 0.3.2 (2026-06-27)

### Security

- **SSRF guard for all daemon-side HTTP outbound** — `fetch`, `download url`, GraphQL, source map loading now validate target IPs against a blocklist (loopback, RFC1918, link-local, cloud metadata). Redirect hops are also validated via httpx event hooks, blocking public→private 302 bypass. Previously only `download url` had this protection.

### Bug Fixes

- **Batch error envelope** — `action_batch` wait step errors now use the standard `{ok, error, hint, action}` envelope instead of bare string. Includes `step_index` and `kind` metadata for agent recovery.
- **Cross-process daemon spawn lock** — auto-start uses an atomic lockfile (`~/.agentcloak/spawn.lock`) to prevent multiple concurrent CLI/MCP processes from spawning duplicate daemons. Stale locks are auto-cleaned.
- **RemoteBridge close cleanup** — `_close_impl` now cancels route/capture tasks, fails pending futures with structured error, and clears pending captures before closing the WebSocket.
- **Snapshot max_chars truncation** — character-level truncation replaced with line-level truncation to preserve complete `[N]` element references. Continuation hint now mentions `--offset` / `--focus`.

### Features

- **Proactive new_tab feedback** — PlaywrightContext listens for popup/`window.open()` events and populates `new_tab` in action results. RemoteBridge accepts `tab_event created` messages (extension-side listener is a follow-up).

## 0.3.1 (2026-06-25)

### Bug Fixes

- Fix installation failure: `httpcloak>=1.6` → `httpcloak>=1.6.0b1` to allow pip to resolve pre-release versions (#1)
- Fix RemoteBridge operations incorrectly routed to local browser in Claude Code sessions

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
