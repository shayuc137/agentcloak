# MCP tools reference

agentcloak's MCP server exposes 38 tools via stdio transport. It is included in the base install (`pip install agentcloak`) and run with `agentcloak-mcp`.

For setup instructions, see the [MCP setup guide](../guides/mcp-setup.md).

## Response shape

Tools return the same human/agent-readable text the CLI prints. The daemon only
emits JSON envelopes; both surfaces share `core/text_renderers` and render
locally, so for any given daemon payload the MCP text output is byte-identical
to `cloak <command>`. Errors stay as the three-field JSON envelope
(`{"error", "hint", "action"}`) — that's the schema MCP clients already parse
for failure handling, so the contract matches the CLI `--json` shape.

`agentcloak_screenshot` is the exception: it returns an MCP `ImageContent` so a
multimodal LLM reads the bytes directly without a base64 round-trip, plus a
short `TextContent` carrying size/format metadata.

## Navigation

### agentcloak_navigate

Navigate the browser to a URL.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | required | Target URL (http:// or https://) |
| `timeout` | `float` | `30.0` | Max seconds to wait for page load |
| `include_snapshot` | `bool` | `false` | Include accessibility tree snapshot in response |
| `snapshot_mode` | `str` | `compact` | Snapshot mode when `include_snapshot` is true |

### agentcloak_snapshot

Get page content as an accessibility tree with `[N]` element references.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | `str` | `compact` | `compact` (default), `accessible`, `content`, or `dom` |
| `selector` | `str` | `""` | Scope the accessibility tree to a main-document CSS selector |
| `max_chars` | `int` | `0` | Truncate tree_text to N characters (0 = no limit) |
| `max_nodes` | `int` | `0` | Truncate after N nodes (0 = no limit) |
| `focus` | `int` | `0` | Expand subtree around element `[N]` |
| `offset` | `int` | `0` | Start from Nth element (pagination) |
| `frames` | `bool` | `false` | Include iframe content |
| `diff` | `bool` | `false` | Mark changes since previous snapshot |

`selector` cannot be combined with `frames=true` or `mode="dom"`. Diff baselines are reused only when mode, selector, and frame settings match.

### agentcloak_screenshot

Take a screenshot of the current page. Returns an `ImageContent` (the image bytes) plus a short `TextContent` with size/format metadata — multimodal LLMs read the pixels directly without a base64 round-trip on the agent side.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `full_page` | `bool` | `false` | Capture full scrollable page |
| `format` | `str \| null` | `null` | Override with `jpeg` or `png`; omitted uses `browser.screenshot_format` |
| `quality` | `int` | `config.mcp_screenshot_quality` | JPEG quality 0-100 (defaults lower than the CLI to fit MCP token budgets) |
| `wait_selector` | `str` | `""` | Wait for this CSS selector to be visible before capture |
| `wait_timeout` | `int \| null` | `null` | Selector wait timeout in ms; omitted uses `browser.action_timeout` |

The returned `format` determines both the `ImageContent` MIME type and metadata.

## Interaction

### agentcloak_action

Interact with the page using `[N]` element references.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `kind` | `str` | required | `click`, `fill`, `type`, `scroll`, `hover`, `select`, `press`, `keydown`, `keyup` |
| `target` | `str` | `""` | Element `[N]` ref (empty for scroll/press/key) |
| `text` | `str` | `""` | Text for fill/type |
| `key` | `str` | `""` | Key for press/keydown/keyup (e.g. `Enter`, `Control+a`) |
| `value` | `str` | `""` | Option value for select |
| `direction` | `str` | `down` | Scroll direction (up/down) |
| `include_snapshot` | `bool` | `false` | Attach compact snapshot to response |

Returns include proactive state feedback: `pending_requests`, `dialog`, `navigation`, `current_value`.

## Content

### agentcloak_evaluate

Execute JavaScript in the browser page context.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `js` | `str` | `""` | JavaScript code to evaluate (omit when using `preset`) |
| `world` | `str` | `main` | `main` (page globals visible) or `isolated` |
| `max_return_size` | `int` | `50000` | Max bytes of serialized result |
| `preset` | `str` | `""` | Reverse-engineering preset (overrides `js`, forced to main world): `vue_inspect`, `react_inspect`, `jwt_decode`, `cookie_parse`, `storage_dump` |

### agentcloak_fetch

HTTP request using the browser's cookies and user agent.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | required | Request URL |
| `method` | `str` | `GET` | HTTP method |
| `body` | `str` | `null` | Request body for POST/PUT |
| `headers_json` | `str` | `null` | Extra headers as JSON object |
| `timeout` | `float` | `30.0` | Timeout in seconds |

## Network

### agentcloak_network

List captured network requests.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `since` | `str` | `0` | Seq number or `last_action` |

## Capture

### agentcloak_capture_control

Control network traffic recording.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | required | `start`, `stop`, `clear`, or `replay` |
| `url` | `str` | `""` | URL for replay action |
| `method` | `str` | `GET` | HTTP method for replay |

### agentcloak_capture_query

Query captured traffic data.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `status` | `status`, `export`, or `analyze` |
| `format` | `str` | `har` | Export format: `har` or `json` |
| `domain` | `str` | `""` | Filter by domain (for analyze) |

## Dialog

### agentcloak_dialog

Handle browser dialogs (alert, confirm, prompt).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `kind` | `str` | `status` | `status`, `accept`, or `dismiss` |
| `text` | `str` | `""` | Reply text for prompt dialogs (with accept) |

## Wait

### agentcloak_wait

Wait for a condition before continuing.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `condition` | `str` | required | `selector`, `url`, `load`, `js`, or `ms` |
| `value` | `str` | `""` | Selector/URL/state/expression/milliseconds |
| `timeout` | `int` | `30000` | Max wait time in ms |
| `state` | `str` | `visible` | Element state for selector: `visible`, `hidden`, `attached`, `detached` |

## Upload

### agentcloak_upload

Upload files to a file input element. Pass `index` to target a visible input, or omit it to auto-find `input[type=file]` elements — including the `display:none` inputs drag-drop uploaders hide — and attach to the `nth` one.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `files` | `list[str]` | required | List of absolute file paths |
| `index` | `int` | `null` | Element `[N]` ref of file input (omit to auto-find) |
| `nth` | `int` | `0` | When auto-finding (no `index`), the nth file input to use (0-based) |

## Frame

### agentcloak_frame

List or switch between page frames.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `kind` | `str` | `list` | `list` or `focus` |
| `name` | `str` | `""` | Frame name to switch to |
| `url` | `str` | `""` | URL substring to match |
| `main` | `bool` | `false` | Switch to main frame |

## Management

### agentcloak_status

Query daemon and browser status.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | `health` | `health` or `cdp_endpoint` |

### agentcloak_launch

Start or restart the browser daemon.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tier` | `str` | `""` | `auto`, `cloak`, `playwright`, or `remote_bridge` |
| `profile` | `str` | `""` | Named browser profile |

### agentcloak_tab

Manage browser tabs.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `list` | `list`, `new`, `close`, or `switch` |
| `tab_id` | `int` | `-1` | Tab ID (for close/switch) |
| `url` | `str` | `""` | URL for new tab |

### agentcloak_profile

Manage browser profiles.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `list` | `create`, `list`, or `delete` |
| `name` | `str` | `""` | Profile name |
| `from_current` | `bool` | `false` | Copy cookies from current session (create only) |

### agentcloak_doctor

Run diagnostic checks on the installation.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fix` | `bool` | `false` | Attempt in-process repairs (download binary, create data dir) |
| `detail` | `bool` | `false` | Show every check (verbose). Default returns concise 2-line summary + runtime status |

Default output: pass count + version + browser description, headless/headed, humanize, proxy, profile.

### agentcloak_resume

Get session resume snapshot for context recovery.

No parameters. Returns current URL, open tabs, last 5 actions, capture state, stealth tier, and timestamp.

## Cookies

### agentcloak_cookies

Manage browser cookies.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `export` | `export` or `import` |
| `url` | `str` | `""` | Filter by URL (export only) |
| `cookies_json` | `str` | `""` | JSON array of cookie objects (import only) |

## Spells

### agentcloak_spell_run

Run a registered spell by name.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Spell name as `site/command` |
| `args_json` | `str` | `{}` | Arguments as JSON object |

### agentcloak_spell_list

List all registered spells.

No parameters. Returns array of spells with site, name, strategy, and description.

## Bridge

### agentcloak_bridge

Manage remote browser tabs via Chrome Extension bridge.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `claim` | `claim` or `finalize` |
| `tab_id` | `int` | `-1` | Chrome tab ID (claim only) |
| `url_pattern` | `str` | `""` | URL substring match (claim only) |
| `mode` | `str` | `close` | Finalize mode: `close`, `handoff`, or `deliverable` |

## Console, downloads & storage

### agentcloak_console

Read captured browser console output or clear the buffer (console.log/warn/error and uncaught exceptions).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `show` | `show` to read messages, `clear` to empty the buffer |
| `since` | `int` | `0` | Only return entries with seq > since (pagination) |
| `limit` | `int` | `0` | Max entries to return (0 = all available) |
| `level` | `str` | `""` | Filter by level: `log`, `warn`, `error`, `info`, `debug` |

### agentcloak_download

Download files — fetch a URL directly or capture a click-triggered download.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `url` | `url` (direct fetch), `wait` (click-triggered), `wait-click` (click `[index]` then await, atomic), or `list` |
| `url` | `str` | `""` | Target URL (action=url only, SSRF-checked) |
| `output_dir` | `str` | `""` | Directory to save into (daemon host) |
| `timeout` | `float` | `0.0` | Max wait seconds for `wait` / `wait-click` |
| `index` | `int` | `0` | Element `[N]` to click (required for `wait-click`) |
| `force` | `bool` | `false` | Bypass overlays with a DOM click (`wait-click` only) |

### agentcloak_storage

Read or write the page's localStorage / sessionStorage.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `get` | `get`, `set`, `delete`, or `clear` |
| `type` | `str` | `local` | `local` (localStorage) or `session` (sessionStorage) |
| `key` | `str` | `""` | Key to read/write/delete (omit for get-all or clear) |
| `value` | `str` | `""` | Value to write (action=set only) |

### agentcloak_clipboard

Read or write the system clipboard.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `read` | `read` or `write` |
| `text` | `str` | `""` | Text to write (action=write only) |

Note: clipboard-read requires a headed browser or RemoteBridge (Chromium blocks it in headless).

### agentcloak_pdf

Export the current page to a PDF file (headless Chromium only).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `output_path` | `str` | required | File path on daemon host |
| `format` | `str` | `A4` | Paper format (A4, Letter, Legal, etc.) |
| `landscape` | `bool` | `false` | Landscape orientation |
| `scale` | `float` | `0.0` | Scale factor (0 = browser default) |
| `page_ranges` | `str` | `""` | e.g. `"1-3, 5"` |

### agentcloak_serve

Serve a local directory over HTTP so you can navigate to local files (`file://` is blocked by security).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `status` | `start`, `stop`, or `status` |
| `directory` | `str` | `""` | Directory to serve (action=start only) |
| `port` | `int` | `0` | Port (0 = auto-assign) |

## Reverse engineering

CDP-backed tools for inspecting and manipulating page internals. Each manager
enables its CDP domain lazily on first use — a session that never reverse-engineers pays nothing — and all of them work on every backend (CloakBrowser, Playwright, RemoteBridge).

### agentcloak_script

Inject JavaScript that runs before page scripts on every navigation — the standard hook point for patching `fetch` / `XHR` / `JSON.parse` before the page uses them (unlike `agentcloak_evaluate`, which runs after load).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `list` | `add`, `remove`, or `list` |
| `js` | `str` | `""` | Raw JavaScript to inject (for `add`) |
| `preset` | `str` | `""` | Built-in hook preset (for `add`; overrides `js`): `fetch`, `xhr`, `json_parse`, `crypto`, `timing` |
| `identifier` | `str` | `""` | Script identifier to remove (for `remove`) |

Presets log the intercepted calls to the console (read with `agentcloak_console`).

### agentcloak_route

Intercept network requests by URL pattern (abort / fulfill / continue). Rules persist across navigations and replay onto new tabs.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `list` | `add`, `remove`, or `list` |
| `pattern` | `str` | `""` | URL glob (`*` = any chars; no `*` = substring match) |
| `rule_action` | `str` | `continue` | Disposition for `add`: `abort`, `fulfill`, or `continue` |
| `resource_type` | `str` | `""` | Only match this resource type (`xhr`, `image`, ...) |
| `method` | `str` | `""` | Only match this HTTP method |
| `status` | `int` | `0` | Response status for a `fulfill` rule (default 200) |
| `content_type` | `str` | `""` | Content-Type for a `fulfill` response |
| `body` | `str` | `""` | Response body for a `fulfill` response |

### agentcloak_headers

Set extra HTTP headers applied to every subsequent request — forge an Authorization token or custom header while debugging an API. Call with no headers to clear the override.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `headers` | `dict[str, str]` | `null` | Header name → value map. Empty/null clears all overrides |

### agentcloak_graphql

Introspect a GraphQL schema or send an arbitrary query through the browser session (inherits the page's cookies and passes the security domain check).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `introspect` | `introspect` or `query` |
| `url` | `str` | `""` | GraphQL endpoint URL |
| `query` | `str` | `""` | GraphQL document (for `query`) |
| `variables` | `dict` | `null` | GraphQL variables object (for `query`) |
| `headers` | `dict` | `null` | Extra request headers (e.g. an auth token) |

### agentcloak_streaming

Capture WebSocket frames and Server-Sent Events — traffic invisible to the ordinary network view. Frames and events land in ring buffers paged by a monotonic seq, like the console.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `ws_messages` | `ws_list`, `ws_messages`, or `sse_messages` |
| `since` | `int` | `0` | Only return frames/events with seq greater than this value |

### agentcloak_debugger

Inspect and control paused JavaScript execution via the CDP Debugger domain: set breakpoints, step, read the call stack and scope. The domain enables lazily on the first `enable` / `breakpoint_set`. While paused, page actions (navigate, click, ...) return a `debugger_paused` error — call `resume` or `step`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `paused_info` | `enable`, `disable`, `breakpoint_set`, `breakpoint_remove`, `breakpoint_list`, `xhr_set`, `xhr_remove`, `resume`, `step`, `paused_info`, `scope_variables`, `evaluate`, `scripts`, `script_source`, `search`, `skip_pauses` |
| `url` | `str` | `""` | Script URL regex (for `breakpoint_set`) |
| `line` | `int` | `0` | Zero-based line number (for `breakpoint_set`) |
| `condition` | `str` | `""` | Break only when this JS expression is truthy |
| `breakpoint_id` | `str` | `""` | Breakpoint id (for `breakpoint_remove`) |
| `url_pattern` | `str` | `""` | XHR URL substring (for `xhr_set` / `xhr_remove`; empty = all XHRs) |
| `step_type` | `str` | `over` | `over`, `into`, or `out` (for `step`) |
| `object_id` | `str` | `""` | Scope objectId from a frame's scopeChain (for `scope_variables`) |
| `call_frame_id` | `str` | `""` | callFrameId (for `evaluate`) |
| `expression` | `str` | `""` | Expression to evaluate in the paused frame |
| `script_id` | `str` | `""` | Script id (for `script_source` / `search`) |
| `query` | `str` | `""` | Substring or regex (for `search`) |
| `is_regex` | `bool` | `false` | Treat `query` as a regex |
| `case_sensitive` | `bool` | `false` | Case-sensitive search |
| `skip` | `bool` | `true` | For `skip_pauses`: ignore all breakpoints / `debugger;` (anti-anti-debug) |

### agentcloak_sourcemap

Discover and parse source maps (pure-Python VLQ decode) to reverse-map a compiled `line:column` back to the original source file and read its text. Builds on the debugger's script inventory.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `list` | `list`, `get`, `lookup`, `sources`, or `source_content` |
| `script_id` | `str` | `""` | CDP script id from the `list` action |
| `line` | `int` | `0` | Zero-based generated (compiled) line (for `lookup`) |
| `column` | `int` | `0` | Zero-based generated column (for `lookup`) |
| `source_path` | `str` | `""` | A path from `sources` (for `source_content`) |

### agentcloak_profiler

JS code coverage, CPU profiling, and heap memory snapshots — find which code ran, which functions burn CPU, and what data sits in memory.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | `coverage_start` | `coverage_start`, `coverage_stop`, `coverage_get`, `cpu_start`, `cpu_stop`, or `heap_snapshot` |
| `script_id` | `str` | `""` | Filter coverage to one script (for `coverage_get`) |
| `output_path` | `str` | `""` | File path for CPU profile JSON or heap snapshot |

Coverage workflow: `coverage_start` → perform actions → `coverage_get` → see per-script function coverage percentages. CPU profiling: `cpu_start` → exercise the page → `cpu_stop` → see ranked hot functions. Heap: `heap_snapshot` streams V8 memory to a `.heapsnapshot` file (loadable in Chrome DevTools).

### agentcloak_performance

Page runtime performance metrics (DOM node count, JS heap size, layout count, etc.).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| (none) | | | Returns all available `Performance.getMetrics` counters |
