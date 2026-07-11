# CLI reference

agentcloak provides two equivalent CLI entry points: `agentcloak` and `cloak` (shorthand). All examples use `cloak`.

## Output convention

Since v0.2.0 the CLI is **text-first**. stdout is the answer itself; stderr carries hints and errors; exit code is `0` on success, `1` on business failure, `2` on bad usage.

Examples:

```text
$ cloak navigate https://example.com
https://example.com/ | Example Domain

$ cloak snapshot
# Example Domain | https://example.com/ | 8 nodes (1 interactive) | seq=2
  heading "Example Domain" level=1
  [1] link "Learn more" href="https://iana.org/domains/example"

$ cloak click 99
Error: Element [99] not in selector_map (1 entries)
  -> run 'snapshot' to refresh the selector_map, or re-snapshot if the page changed
```

For programmatic consumers (scripts, jq pipelines, MCP-style integrations) opt back into the legacy envelope:

```bash
# --json flag (any position)
cloak --json snapshot | jq -r '.data.tree_text'

# AGENTCLOAK_OUTPUT env var (no flag changes for CI / wrappers)
AGENTCLOAK_OUTPUT=json cloak snapshot
```

Envelope shape (only when `--json` is active):

```json
{"ok": true, "seq": 3, "data": {...}}
{"ok": false, "error": "error_code", "hint": "description", "action": "suggested next step"}
```

## Global flags

| Flag | Effect |
|------|--------|
| `--json` | Switch to JSON envelope output for the whole command |
| `--pretty` | Indent JSON output (no-op without `--json`; warns on stderr) |
| `--verbose` / `-v` | Raise log level (`-v` info, `-vv` debug) |
| `--version` | Print version and exit |
| `AGENTCLOAK_OUTPUT=json` env var | Same as `--json`, no flag rewrite needed |

## Navigation and observation

### navigate

Navigate the browser to a URL.

```bash
cloak navigate URL [--timeout SECONDS] [--snap] [--snapshot-mode MODE]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--timeout` | `30` | Max seconds to wait for page load |
| `--snap` (alias `--snapshot`) | off | Attach a compact snapshot to the result (saves a round-trip) |
| `--snapshot-mode` | `compact` | Snapshot mode when `--snap` is set (`compact` or `accessible`) |

### snapshot

Get the page as an accessibility tree with `[N]` element references.

```bash
cloak snapshot [--mode MODE] [--selector CSS] [--limit N] [--focus N] [--offset N] [--frames] [--diff] [--selector-map]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--mode` | `compact` | `compact` (default), `accessible`, `content`, `dom` |
| `--selector` (aliases `--within`, `-s`) | none | Scope the accessibility tree to a main-document CSS selector |
| `--limit` (alias `--max-nodes`) | `0` | Truncate after N nodes (0 = no limit) |
| `--focus` | `0` | Expand subtree around element `[N]` |
| `--offset` | `0` | Start output from Nth element (pagination) |
| `--frames` | off | Include iframe content |
| `--diff` | off | Mark changes since previous snapshot |
| `--selector-map` | off | Include the raw selector_map (debug / scripting) |

`--selector` scopes the tree before `[N]` refs are assigned, keeping refs and output limited to the selected subtree. It cannot be combined with `--frames` or `--mode dom`.

Output starts with a header line:

```text
# <title> | <url> | <total_nodes> nodes (<interactive> interactive) | seq=<n>
```

### screenshot

Take a screenshot of the current page.

```bash
cloak screenshot [--output FILE] [--full-page] [--format FORMAT] [--quality N]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--output` | auto-named in OS temp dir (`tempfile.gettempdir()`) | Save to file; stdout prints the path |
| `--full-page` | off | Capture full scrollable page |
| `--format` | `jpeg` | `jpeg` or `png` |
| `--quality` | `80` | JPEG quality 0-100 (ignored for PNG) |

> [!TIP]
> **When to use PNG vs JPEG:**
> - `--format png` — UI design verification, OCR, vision models. Lossless quality
>   avoids JPEG artefacts that confuse text recognition or pixel-level comparison.
> - `--format jpeg` (default) — layout checks, page state verification. Ships
>   ~4-10× smaller payloads, good enough when pixel fidelity doesn't matter.
>
> MCP tools default to JPEG quality 50 (configurable via `browser.mcp_screenshot_quality`)
> to stay within token budgets. CLI defaults to quality 80.

### resume

Get session state for context recovery.

```bash
cloak resume
```

Returns current URL, open tabs, last 5 actions, capture state, and stealth tier.

## Interaction

All interaction commands accept the element index positionally (`cloak click 5`) or via `--index N` / `-i N`. Most also take a positional secondary value (`cloak fill 5 "query"`).

Add `--snap` to any interaction to attach a compact snapshot to the response.

### click

Click an element by `[N]` reference.

```bash
cloak click N [--snap]
cloak click --index N [--snap]
cloak click --x X --y Y           # coordinate fallback
cloak click N --force             # skip the pointer-intercept check (covering overlay)
```

When an element is hidden behind an overlay, retry with `--force`: Playwright/Cloak bypass actionability checks and RemoteBridge invokes the resolved DOM element's `click()` instead of coordinate hit testing. Fall back to `js evaluate "document.querySelector('...')?.click()"` when a page requires a custom event path.

### fill

Clear an input field and set its value.

```bash
cloak fill N "value" [--snap]
cloak fill --index N --text "value" [--snap]
```

`fill` uses framework-compatible value setters. RemoteBridge calls the native
input/textarea/select prototype setter before bubbling `input` and `change`, so
React/Vue controlled fields receive the update.

### type

Type text character by character (triggers key events).

```bash
cloak type N "value" [--snap]
```

### press

Press a keyboard key or key combination.

```bash
cloak press KEY [N] [--snap]
cloak press --key KEY [--index N] [--snap]
```

Key names use Playwright syntax: `Enter`, `Tab`, `Escape`, `Control+a`, `Shift+ArrowDown`.

### scroll

Scroll the page.

```bash
cloak scroll DIRECTION [--snap]
cloak scroll --direction DIRECTION
```

Direction: `up` or `down`.

### hover

Hover over an element.

```bash
cloak hover N [--snap]
```

### select

Select a dropdown option.

```bash
cloak select N --value "option" [--snap]
```

## Content and network

### js evaluate

Execute JavaScript in the page context.

```bash
cloak js evaluate "expression"
cloak js evaluate --file probe.js          # multiline UTF-8 script, no shell quoting
cloak js evaluate --preset vue_inspect    # run a reverse-engineering preset instead of JS
```

Scalar results (string/number/boolean) print as raw values. Objects and arrays print as pretty JSON.
Inline code, `--file`, and `--preset` are mutually exclusive. Evaluation failures
report the thrown message and first useful source/stack location, bounded to 400
characters so a page cannot flood agent context with a stack trace.

`--preset` runs a canned reverse-engineering snippet (forced to the main world, so leave the JS argument empty) and returns parsed JSON:

| Preset | Output |
|--------|--------|
| `vue_inspect` | Vue 2/3 components with `$data` / props / method / computed key names |
| `react_inspect` | React component tree (names + props/state keys, depth-capped) |
| `jwt_decode` | JWTs found in cookies / localStorage / sessionStorage, decoded header + payload |
| `cookie_parse` | structured `document.cookie` (name/value) |
| `storage_dump` | full localStorage + sessionStorage dump |

A mistyped preset returns an `unknown_preset` error listing the valid names.

### fetch

HTTP request using the browser's cookies and user agent. The response body goes to stdout; status / headers go to stderr.

```bash
cloak fetch URL [--method METHOD] [--body BODY] [--headers-json JSON]
```

### network requests

List recent network requests.

```bash
cloak network requests [--since SEQ]
```

Use `--since last_action` to see requests triggered by the most recent action.

### network console

List console messages.

```bash
cloak network console [--since SEQ]
```

## Dialog handling

```bash
cloak dialog status                # check for pending dialogs
cloak dialog accept [--text "reply"]
cloak dialog dismiss
```

## Waiting

```bash
cloak wait --selector "CSS_SELECTOR"
cloak wait --url "**/dashboard"
cloak wait --load networkidle
cloak wait --js "document.readyState === 'complete'"
cloak wait --ms 2000
```

| Flag | Description |
|------|-------------|
| `--selector` | Wait for CSS selector to appear |
| `--url` | Wait for URL pattern (glob) |
| `--load` | Wait for load state (`load`, `domcontentloaded`, `networkidle`) |
| `--js` | Wait for JS expression to return truthy |
| `--ms` | Sleep for N milliseconds |
| `--timeout` | Max wait time in ms (default 30000) |

### Common recipes

```bash
# Wait for web fonts to load before taking a screenshot
cloak wait --js "document.fonts.ready.then(() => true)"
cloak screenshot --format png

# Wait for all network activity to settle (SPA hydration, lazy-loaded data)
cloak wait --load networkidle

# Wait for a specific API response before extracting data
cloak wait --js "window.__DATA_LOADED === true"

# Combine: navigate, wait for fonts + network idle, then screenshot
cloak navigate "https://example.com"
cloak wait --load networkidle
cloak wait --js "document.fonts.ready.then(() => true)"
cloak screenshot --format png --full-page
```

> [!TIP]
> `--js` expressions must return a truthy value. For Promises like
> `document.fonts.ready`, wrap them: `.then(() => true)`.

## File upload

```bash
cloak upload --index N --file /path/to/file [--file /path/to/another]
cloak upload --file /path/to/file                  # auto-find hidden file inputs
cloak upload --file /path/to/file --nth 1          # pick the 2nd file input
```

With `--index` it targets a specific snapshot `[N]` ref. Omit `--index` and the daemon auto-finds every `input[type=file]` on the page — including the `display:none` inputs drag-drop uploaders (Dropzone, react-dropzone, Ant Upload) hide from the accessibility tree — and attaches to the `--nth` one (0-based, default 0). The response reports `candidates_count` and `used_nth`, so if it picked the wrong input you can re-issue with a different `--nth`. When no file input exists the command returns `no_file_input_found`; an out-of-range `--nth` returns `file_input_index_out_of_range`.

## Downloads

```bash
cloak download url URL [--output DIR]              # direct fetch with browser cookies (SSRF-checked)
cloak download wait [--output DIR] [--timeout S]   # block for the next click-triggered download
cloak download wait-click --index N [--force]      # click [N] and await the download, atomically
cloak download list                                # downloads saved this session
```

Files are saved on the daemon host (default: system temp dir). `wait-click` arms the download waiter, clicks `[N]`, and awaits completion in one request — use it when a button or link triggers the download, since a single-threaded agent can't run `download wait` and `click` concurrently. A failing click reports immediately instead of hanging until the download times out; pass `--force` to skip the pointer check on an obscured trigger.

## Frame management

```bash
cloak frame list
cloak frame focus --name "frame-name"
cloak frame focus --url "partial-url"
cloak frame focus --main
```

## Reverse engineering

CDP-backed inspection and manipulation. Each capability enables its CDP domain lazily on first use, so a session that never reverse-engineers pays nothing. All commands work on every backend (CloakBrowser, Playwright, RemoteBridge).

### Init scripts

Inject JavaScript that runs before page scripts on every navigation — the hook point for patching `fetch` / `XHR` / `JSON.parse`.

```bash
cloak script add "JS"                 # inject raw JS; prints an identifier
cloak script add --preset fetch       # built-in hook: fetch|xhr|json_parse|crypto|timing
cloak script remove ID
cloak script list
```

Presets log intercepted calls to `cloak console`.

### Network route interception

Intercept requests by URL pattern. Rules persist across navigations and replay onto new tabs.

```bash
cloak route add "**/api/*" --action abort
cloak route add "**/track" --action fulfill --status 204 --content-type application/json --body "{}"
cloak route add "*" --action continue --resource-type xhr --method POST
cloak route remove "**/api/*"         # omit pattern to clear ALL rules
cloak route list
```

### Extra HTTP headers

```bash
cloak emulation headers -H "Authorization: Bearer TOKEN" -H "X-Requested-With: XMLHttpRequest"
cloak emulation headers               # no -H clears all overrides
```

### GraphQL

Runs through the browser session (cookies + security domain check).

```bash
cloak graphql introspect https://api.example.com/graphql
cloak graphql query https://api.example.com/graphql "query { me { id } }" --variables '{"id": 1}'
cloak graphql query URL QUERY -H "Authorization: Bearer TOKEN"
```

### Streaming (WebSocket + SSE)

Capture traffic invisible to `network requests`. Buffers page by a monotonic seq.

```bash
cloak ws list                          # tracked WebSocket connections
cloak ws messages [--since SEQ]        # → sent, ← received frames
cloak sse messages [--since SEQ]       # Server-Sent Events
```

### Debugger

Set breakpoints, step, read the call stack and scope. The domain enables lazily; while paused, page actions return `debugger_paused` until `resume` / `step`.

```bash
cloak debugger enable
cloak debugger breakpoint-set "main\.js" 42 --condition "x > 1"   # URL regex + zero-based line
cloak debugger breakpoint-remove ID
cloak debugger breakpoint-list
cloak debugger xhr-set "/api/login"    # break on matching XHR (omit pattern = all XHRs)
cloak debugger xhr-remove "/api/login"
cloak debugger paused-info             # reason + call stack (callFrameIds in brackets)
cloak debugger step --type over        # over | into | out
cloak debugger resume
cloak debugger scope-variables OBJECT_ID
cloak debugger evaluate CALL_FRAME_ID "expr"
cloak debugger scripts                 # parsed scripts (id, URL, source-map marker)
cloak debugger script-source SCRIPT_ID
cloak debugger search SCRIPT_ID "query" --regex --case-sensitive
cloak debugger search --url "main.js" "query"   # match scripts by URL substring (id-free; survives navigation)
cloak debugger skip-pauses true        # ignore all breakpoints / debugger; (anti-anti-debug)
```

Pass either a `SCRIPT_ID` (from `debugger scripts`) or `--url` (a URL substring). Script ids are invalidated by navigation, so `--url` is the durable way to search a bundle by filename — it searches every matching script and groups the hits by URL.

### Source maps

Reverse-map compiled positions back to original source. Requires the debugger enabled.

```bash
cloak sourcemap list                   # scripts that declared a sourceMapURL
cloak sourcemap get SCRIPT_ID          # download + parse; metadata summary
cloak sourcemap lookup SCRIPT_ID --line N --column N   # compiled pos → original source:line:col
cloak sourcemap sources SCRIPT_ID      # original source file paths
cloak sourcemap source-content SCRIPT_ID SOURCE_PATH
```

### Profiling

JS code coverage, CPU profiling, performance metrics, and heap snapshots.

```bash
cloak profiler coverage-start              # begin recording function-level coverage
cloak profiler coverage-stop               # stop recording
cloak profiler coverage-get                # per-script summary (functions total/covered/%)
cloak profiler coverage-get --script-id ID # single script with per-function detail
cloak profiler cpu-start                   # begin CPU sampling
cloak profiler cpu-stop                    # stop and show top functions by self time
cloak profiler cpu-stop --output profile.cpuprofile  # save raw profile (opens in DevTools)
cloak profiler heap-snapshot --output snap.heapsnapshot  # V8 heap dump to file
cloak performance metrics                  # DOM nodes, JS heap, layout counts
```

## Capture and spells

```bash
cloak capture start
cloak capture stop
cloak capture status
cloak capture export --format har > traffic.har
cloak capture export --format json
cloak capture analyze [--domain example.com]
cloak capture clear

cloak spell list
cloak spell info NAME
cloak spell run NAME [key=value ...]
cloak spell scaffold SITE COMMAND
```

`capture export` writes the raw HAR/JSON to stdout — pipe to a file. `spell run` prints the spell's return value directly (no envelope).

## Profile management

```bash
cloak profile create NAME [--from-current]
cloak profile list
cloak profile launch NAME
cloak profile delete NAME
```

## Tab management

```bash
cloak tab list                    # git-branch style: * marks active
cloak tab new [--url URL]
cloak tab close --tab-id N
cloak tab switch --tab-id N
```

## Bridge commands

```bash
cloak bridge claim --tab-id N
cloak bridge claim --url "dashboard"
cloak bridge finalize --mode close        # close agent tabs
cloak bridge finalize --mode handoff      # leave tabs for user
cloak bridge finalize --mode deliverable  # rename group to "results"
cloak bridge token                        # print the persistent auth token
cloak bridge token --reset                # rotate the token
```

`cloak bridge token` prints the raw token to stdout — easy to pipe into other tools.

## Cookie management

```bash
cloak cookies export                              # every cookie in the active browser
cloak cookies export --url https://example.com    # only cookies that match the URL
cloak cookies import -c '[{"name":"token","value":"abc","domain":".example.com","path":"/"}]'
```

`cookies export` prints `domain | name=value` lines (one cookie per line) so an
agent grepping the output can tell which site each cookie came from. Pass
`--url` to scope the export to a single site — recommended whenever the agent
only needs credentials for one domain, since the unfiltered output includes
sessions for every site loaded in the active browser. `cookies import` accepts
the structured JSON form so httpOnly cookies survive.

## Daemon management

```bash
cloak daemon start [--host HOST] [--port PORT] [--headed] [--profile NAME]
cloak daemon stop
cloak daemon status                # tier | browser status | seq (+ metrics line)
```

`daemon status` (and the MCP `agentcloak_status`) prints a second line with the
daemon's liveness metrics — `uptime <duration> | <N> requests | <N> active` —
so it doubles as a lightweight monitoring readout. The line is omitted when the
daemon predates the metrics fields.

## Session management

A single daemon serves several callers concurrently (two Claude Code sessions, an MCP client, plain CLI runs). Each caller is routed to its own isolated browser by the `X-Agentcloak-Session` header. The session id is auto-detected — `AGENTCLOAK_SESSION` > `CLAUDE_CODE_SESSION_ID` > `default` — so concurrent agents get separate browsers with zero configuration. A named session's browser is suspended after `daemon.session_idle_timeout` seconds of inactivity (default 300s) and transparently rebuilt on the next request.

```bash
cloak session list                 # named sessions: id | state (active/suspended) | tier | idle
cloak session close SESSION_ID     # close a session and free its browser now
```

Header-less calls (every plain CLI invocation) use the `default` session backed by the daemon's primary browser, which is not listed here — its state shows up in `cloak status` / `/health`.

## Configuration

```bash
cloak config                       # alias for 'config list'
cloak config list                  # key = value (source) — git-config -l style
cloak config get <key>             # print one value
cloak config set <key> <val...>    # set scalar or replace list (batch: k1 v1 k2 v2)
cloak config add <key> <val...>    # append to list-typed key
cloak config remove <key> <val>    # remove from list-typed key
cloak config unset <key>           # reset to default
cloak config keys                  # list all settable dot-notation keys
```

Keys use dot-notation (e.g., `browser.proxy`, `browser.extra_args`). Types are inferred from the config schema -- `add`/`remove` only work on list fields. Browser/daemon changes print a restart hint.

See [config reference](config.md) for all available keys and environment variables.

## Diagnostics

```bash
cloak doctor                       # concise summary + runtime status (2 lines)
cloak doctor --detail              # verbose per-check [ok]/[fail] lines
cloak doctor --fix                 # attempt in-process repair (binary download, data dir)
cloak doctor --fix --sudo          # also run the synthesised system command via sudo

cloak cdp endpoint                 # raw ws:// URL for jshookmcp / other CDP tools
```

`doctor` exits with code `1` when any check fails, so it composes with shell scripts.
