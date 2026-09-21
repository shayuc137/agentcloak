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
Error [element_not_found]: Element [99] not in selector_map (1 entries)
  -> run 'snapshot' to refresh the selector_map, or re-snapshot if the page changed
```

For scripts and jq pipelines, enable structured output:

```bash
# --json flag (any position)
cloak --json snapshot | jq -r '.data.tree_text'

# AGENTCLOAK_OUTPUT env var (no flag changes for CI / wrappers)
AGENTCLOAK_OUTPUT=json cloak snapshot
```

Envelope shape (only when `--json` is active):

```json
{"ok": true, "seq": 3, "data": {...}}
{"ok": false, "error": {"code": "error_code", "message": "description"}, "hint": "description", "action": "suggested next step"}
```


Failures emit one JSON envelope on stdout and an `Error [code]: message` diagnostic on stderr. This also covers missing arguments and local validation failures. `js evaluate` throws and rejected promises fail; a returned string beginning with `Error:` remains ordinary data. Diagnostic failures can include their check results under `data`.

| `error.code` | Meaning |
|--------------|---------|
| `invalid_request` | Invalid CLI arguments or daemon request validation |
| `command_failed` | Local command validation failed |
| `command_aborted` | Command interrupted or cancelled |
| `internal_error` | Unexpected CLI or daemon exception |
| `config_error` | Invalid configuration key or value |
| `doctor_failed`, `bridge_check_failed` | Environment or bridge checks failed; JSON includes diagnostic `data` |
| `skill_uninstall_failed` | A skill file operation failed |
| `daemon_unreachable`, `daemon_timeout` | Daemon connection or request timeout |
| `daemon_invalid_response`, `daemon_request_failed` | Invalid response body or failed daemon request |
| `evaluate_failed` | JavaScript syntax/runtime exception on a local backend |
| `cdp_timeout`, `cdp_call_failed` | Raw CDP timeout or protocol failure |

Other domain codes, such as `element_not_found`, pass through unchanged. Direct daemon responses and MCP errors retain the string `error` plus `hint` and `action`; the nested `error.code/message` shape belongs to CLI JSON output.

## Global flags

| Flag | Effect |
|------|--------|
| `--workspace PATH` | Use this root and its descendants as the workspace (accepted before or after the command) |
| `--session ID` | Bind this invocation to a named browser session (accepted before or after the command) |
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

A simple `#fragment` waits up to 3 seconds for an element with that id and scrolls it into view, covering late-rendered SPA anchors. A miss keeps navigation successful and prints `[anchor] not found`. Hashbang routes and parameter-like fragments containing `=`, `&`, or `/` are left to the application.

### snapshot

Get the page as an accessibility tree with `[N]` element references. Both `compact` and `accessible` include exposed `button`/`menuitem` roles and focusable custom elements, including `tabindex="0"` and `tabindex="-1"`. Ignored AX nodes remain excluded; open a collapsed menu before taking a new snapshot.

```bash
cloak snapshot [--mode MODE] [--selector CSS] [--limit N] [--focus N] [--offset N] [--frames] [--diff] [--hide CSS] [--keep-overlays]
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
| `--hide` | none | Comma-separated CSS selectors to hide for this snapshot |
| `--keep-overlays` | off | Reveal persistent, one-time, and `[data-cloak-hide]` overlays for this snapshot |

`--selector` scopes the tree before `[N]` refs are assigned, keeping refs and output limited to the selected subtree. It cannot be combined with `--frames` or `--mode dom`.

Output starts with a header line:

```text
# <title> | <url> | <total_nodes> nodes (<interactive> interactive) | seq=<n>
```

### viewport

```bash
cloak viewport set 2560x1440 --dpr 2
cloak screenshot --viewport 1024x768 --dpr 2 --output compact.png
```

`viewport set` changes only the current session's page without navigation or loss of login state. Omitting `--dpr` preserves the current device pixel ratio. DPR must be finite and positive. Screenshot overrides are temporary and restore both dimensions and DPR on success, failure or cancellation, including prior raw-CDP overrides. Dimensions are CSS pixels, positive integers up to 16384; a 600×400 capture at DPR 2 produces 1200×800 image pixels. `--dpr` can also be used without `--viewport`.

### emulate

```bash
cloak emulate --color-scheme dark --reduced-motion
cloak emulate --color-scheme light --no-reduced-motion
cloak emulate --pointer coarse
cloak emulate --pointer fine
cloak emulate
cloak emulate reset
```

Local Playwright and CloakBrowser sessions support color scheme and reduced motion in both headed and headless modes. Pointer emulation requires `browser.headless=false` (Xvfb is supported); headless requests fail before applying any settings because Chromium cannot reliably restore its desktop pointer baseline. `coarse` enables one touch point; `fine` disables touch emulation. This does not change the user agent or enable mobile viewport layout.

Omitted options retain their overrides. No options shows the current overrides (`null` means browser default). Changes apply to all owned tabs and new tabs; popups inherit after registration, so their earliest scripts may run before emulation is applied. Settings survive navigation but not session closure or daemon restart. Other sessions are unaffected. `reset` clears these overrides without changing viewport, DPR or HTTP headers; it cannot be combined with settings. RemoteBridge returns `unsupported_operation` for changes.

HTTP: `POST /emulation` with optional `color_scheme`, `reduced_motion`, `pointer`, `reset`; MCP: `agentcloak_emulate` with the same fields. Viewport DPR uses `POST /viewport` / `agentcloak_viewport`; temporary screenshot DPR uses `GET /screenshot` / `agentcloak_screenshot`.

### screenshot

Take a screenshot of the current page.

```bash
cloak screenshot [--output FILE] [--viewport WIDTHxHEIGHT] [--dpr RATIO] [--full-page] [--format FORMAT] [--quality N] [--wait-for CSS] [--hide CSS] [--keep-overlays]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--output` | auto-named in OS temp dir (`tempfile.gettempdir()`) | Save to file; `.png` selects PNG and `.jpg`/`.jpeg` selects JPEG |
| `--viewport` | current page | Temporary `WIDTHxHEIGHT`, restored after capture |
| `--dpr` | current page | Temporary device pixel ratio, restored after capture |
| `--full-page` | off | Capture full scrollable page |
| `--format` | output suffix, then `browser.screenshot_format` (`jpeg`) | Explicit `jpeg` or `png` override; must agree with a recognized suffix |
| `--quality` | `80` | JPEG quality 0-100 (ignored for PNG) |
| `--wait-selector` | none | Wait for a CSS selector to be visible before capture |
| `--wait-for` | none | Run the regular visible-selector wait before capture; timeout short-circuits without writing a file |
| `--wait-timeout` | `browser.action_timeout` | Selector wait timeout in milliseconds |
| `--hide` | none | Comma-separated CSS selectors to hide for this capture |
| `--keep-overlays` | off | Reveal persistent, one-time, and `[data-cloak-hide]` overlays for this capture |

> [!TIP]
> **When to use PNG vs JPEG:**
> - `-o page.png` — UI design verification, OCR, vision models. Lossless quality
>   avoids JPEG artefacts that confuse text recognition or pixel-level comparison.
> - `-o page.jpg` — layout checks, page state verification. Ships
>   ~4-10× smaller payloads, good enough when pixel fidelity doesn't matter.
>
> Recognized output suffixes select the encoding without `--format`. An unknown
> non-empty suffix warns before falling back to the live
> `browser.screenshot_format`; an extensionless path falls back quietly. MCP tools
> use JPEG quality 50 (configurable via `browser.mcp_screenshot_quality`); CLI uses
> quality 80.

### diff screenshot

Compare a local baseline with another local image or a fresh live-page PNG.

```bash
cloak diff screenshot BASELINE [--current FILE] [--threshold 0..255] [--output DIFF.png]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--current` | live page | Local current image; when omitted, capture PNG from the active browser |
| `--threshold` | `0` | Ignore per-channel differences at or below this value |
| `--output` | none | Write an RGBA diff with changed pixels highlighted red |

Text output is one stable line:

```text
diff 12/921600 pixels (0.001302%) | max_delta=41 | 1280x720 | threshold=0
```

`--json` adds exact ratio/percentage, dimensions, threshold, maximum channel
delta, baseline/current paths, and the optional output path. Different pixels
still produce exit code 0; DOS or CI owns pass/fail policy.

### resume

Get session state for context recovery.

```bash
cloak resume
```

Returns current URL, open tabs, last 5 actions, capture state, and stealth tier.

## Interaction

All interaction commands accept the element index positionally (`cloak click 5`) or via `--index N` / `-i N`. Most also take a positional secondary value (`cloak fill 5 "query"`).

Positional references accept both `12` and `'[12]'`. Quote bracket references in shells such as zsh to prevent glob expansion.

Add `--snap` to any interaction to attach a compact snapshot to the response.

### click

Click an element by `[N]` reference.

```bash
cloak click N [--snap]
cloak click --index N [--snap]
cloak click --x X --y Y           # coordinate fallback
cloak click N --force             # one-off single-left-click DOM fallback
cloak click N --click-count 2     # double-click
```

For a known overlay, add its selector with `cloak hide add CSS`, re-snapshot, and use a normal click; hiding also cleans screenshots and snapshot output. `--force` is the fallback for an unknown one-off obstruction and invokes the resolved DOM element's `click()` instead of coordinate hit testing. It only supports a single left click: combining it with a non-default `--button` or `--click-count` returns `invalid_argument`.

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

Modifier aliases are case-insensitive: `Ctrl` → `Control`, `Cmd`/`Command` → `Meta`, `Opt`/`Option` → `Alt`. For example, `cloak press Ctrl+Enter`.

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
cloak hover --at 100,200
cloak hover '[12]' --offset 10,-5
```

`--offset` is relative to the element center; `--at` is an absolute viewport coordinate.

### drag

```bash
cloak drag '[4]' '[5]' --steps 12
cloak drag --from 100,200 --to 300,400 --steps 12
```

Drag uses browser pointer input, including the press, intermediate moves, and release. Supply either two references or both coordinate endpoints.

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

# Simple SPA anchors are polled for up to 3 seconds and scrolled into view
cloak navigate "https://example.com/settings#billing"
cloak screenshot --wait-for "#billing"

# Combine: navigate, wait for fonts + network idle, then screenshot
cloak navigate "https://example.com"
cloak wait --load networkidle
cloak wait --js "document.fonts.ready.then(() => true)"
cloak screenshot --format png --full-page
```

Missing anchors do not fail navigation and print `[anchor] not found`. Hashbang routes and parameter-like fragments containing `=`, `&`, or `/` skip anchor handling.

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

Presets log intercepted calls to `cloak console`. `script list` reports whether each script has been injected into the current page; newly registered scripts run before page scripts on the next navigation.

### Network route interception

Intercept requests by URL pattern. Rules persist across navigations and replay onto new tabs.

```bash
cloak route add "**/api/*" --action abort
cloak route add "**/track" --action fulfill --status 204 --content-type application/json --body "{}"
cloak route add "*" --action continue --resource-type xhr --method POST
cloak route remove "**/api/*"         # omit pattern to clear ALL rules
cloak route list
```

`route list` includes rule ids, hit counts, and pending request ids. Zero hits produce a warning, so a registered rule is not mistaken for a verified interception. A pattern without `*` is a URL substring; `*` matches across `/`.

```bash
cloak route add --hold "/api/orders"
# Trigger the request, then inspect the loading state:
cloak snapshot
cloak screenshot --output loading.png
cloak route list
cloak route release RULE_OR_REQUEST_ID
```

Release resumes pending requests; the rule remains installed for future requests. Remove the rule when finished. Held requests do not block snapshot/screenshot. Console messages accumulate across navigation with timestamps and page URLs; `cloak console clear` explicitly empties the buffer (`console show --clear` remains supported).

### Raw CDP

```bash
cloak cdp send Runtime.evaluate --params '{"expression":"document.title","returnByValue":true}' --timeout 1000
```

Commands target the current session's page. The timeout is per request in milliseconds; protocol errors and timeouts exit nonzero with structured errors in JSON mode. Local raw CDP calls share a dedicated per-tab channel. Timeout/cancellation resets that channel, so reapply any CDP state it held; manager subscriptions remain active.

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

`capture export` writes the raw HAR/JSON to stdout — pipe to a file. In text mode,
`spell run` prints the return value directly; `--json` uses the standard envelope.
PUBLIC spells run locally without a daemon. COOKIE, HEADER, INTERCEPT, and UI spells
route through `/spell/run` and use the caller's current Agentcloak session.

## Profile management

```bash
cloak profile create NAME [--from-current]
cloak profile list
cloak profile launch NAME
cloak profile delete NAME
```

`--from-current` seeds the new profile from the active browser: cookies land in
`cookies-snapshot.json` and each origin's `localStorage` lands in
`localStorage-snapshot.json` — the same snapshot files a live session would
maintain, so `cloak profile launch NAME` restores both on first navigation.

A profile directory may also hold a `config.toml` overlay — see [config
reference](config.md#per-profile-config-overlay).

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
cloak cookies restore                             # restore the active profile snapshot
cloak cookies restore --file /tmp/cookies.json    # restore an explicit snapshot
```

`cookies export` prints `domain | name=value` lines (one cookie per line) so an
agent grepping the output can tell which site each cookie came from. Pass
`--url` to scope the export to a single site — recommended whenever the agent
only needs credentials for one domain, since the unfiltered output includes
sessions for every site loaded in the active browser. Without `--output`, export
also refreshes `<profile>/cookies-snapshot.json`, or
`~/.agentcloak/cookies-snapshot.json` when no profile is active. `restore` imports
that file, then `cloak press Control+r` reloads the page with the recovered login state.

`cookies import` and `restore` normalize Chrome cookies API, CDP, and Playwright
cookie shapes. Unknown fields are discarded, `sameSite` and expiry fields are
normalized, and malformed entries are skipped with a reported count instead of
aborting the entire import.

## Page hiding

```bash
cloak hide add ".feedback-toolbar"       # profile-persistent, or session-only without a profile
cloak hide list                           # stable id + selector + [source]
cloak hide remove ID_OR_EXACT_SELECTOR
```

`hide list` output tags each selector with its origin — `[builtin]` for the
immutable `[data-cloak-hide]` rule, `[profile]` for selectors persisted in the
active profile's `hide.json`, `[session]` for ephemeral ones from the current
session (including one-time `--hide` selectors):

```text
$ cloak hide list
scope: work
data-cloak-hide: [data-cloak-hide] [builtin]
feedback-toolbar: .feedback-toolbar [profile]
h1234abcd: .promo-modal [session]
```

Persistent selectors, one-time `--hide` selectors, and the page-owned
`[data-cloak-hide]` attribute all remove matching elements from snapshots,
screenshots, and click hit-testing. `--keep-overlays` reveals all three layers
for one snapshot or screenshot. Profile selectors are stored in `hide.json`;
the builtin `[data-cloak-hide]` rule cannot be removed.

## Launch

Hot-switch the daemon's active browser tier (and optionally its profile) without restarting the daemon.

```bash
cloak launch --tier cloak                 # hot-switch tier, keep current profile
cloak launch --tier playwright --profile work   # switch tier and load a profile
cloak launch --tier cloak --no-profile    # explicitly drop the current profile
```

| Flag | Default | Description |
|------|---------|-------------|
| `--tier` / `-t` | `auto` | Backend: `auto` (→ `cloak`), `cloak`, `playwright`, `remote_bridge` |
| `--profile` / `-p` | keep current | Load a named profile (local tiers only; ignored for `remote_bridge`) |
| `--no-profile` | off | Explicitly switch to no profile, discarding the current one |

Omitting `--profile` keeps whatever profile the daemon is currently attached to — a plain `cloak launch --tier cloak` no longer silently drops it. Pass `--no-profile` to detach the named profile; shared mode then uses ephemeral storage, while workspace mode still persists under its workspace identity; `--profile` and `--no-profile` are mutually exclusive and passing both raises a usage error.

## Daemon management

```bash
cloak daemon start [--host HOST] [--port PORT] [--headed] [--profile NAME] [--log-level info]
cloak daemon stop
cloak daemon status                # tier | browser status | seq (+ metrics line)
```

`daemon status` (and the MCP `agentcloak_status`) prints a second line with the
daemon's liveness metrics — `uptime <duration> | <N> requests | <N> active` —
so it doubles as a lightweight monitoring readout. The line is omitted when the
daemon predates the metrics fields.

`hide` only hides configured matching DOM elements; it does not remove arbitrary overlays injected by external CDP tools. Add a selector explicitly when needed.

## Session management

One daemon hosts workspace-scoped sessions. Each session owns its tabs, references, viewport, routes, scripts and console buffer. Commands queue per session; held navigation permits snapshot/screenshot observation and route release. Sessions in different workspaces remain distinct even with the same explicit ID.

`--session ID` overrides `AGENTCLOAK_SESSION`, then defaults to a canonical worktree/root path hash. `--workspace PATH` overrides `AGENTCLOAK_WORKSPACE`, configured `browser.workspace_roots`, Git repository identity and finally a non-Git working directory. Matching configured roots include subdirectories. MCP servers keep process-scoped sessions in their startup workspace. See [workspace configuration](config.md#workspace-isolation) for storage sharing, persistence and recovery limits.

```bash
cloak navigate http://localhost:5173 --session panel-a
cloak screenshot --session panel-a
cloak session list                     # id | state | tier | idle
cloak session close                    # close only the current caller's session
cloak session close panel-a            # explicitly close this session
```

An idle session releases its own tabs after `daemon.session_idle_timeout` seconds (default 300s); the next request recreates them. A closed page or disconnected local browser is rebuilt on the next request. A shared browser failure loses volatile page state, so page actions report `page_recreated` until a new navigation. Changing the shared tier/profile while other sessions are active is rejected rather than changing those callers' browser. RemoteBridge does not silently share its user tab between callers.

Clients discover the daemon by probing the recorded `/health` endpoint. They only read `daemon.json`; PID visibility or a read-only state directory does not invalidate a live daemon. Raw HTTP callers can send `X-Agentcloak-Workspace` and `X-Agentcloak-Session`; omitted headers use the legacy empty workspace and `default` session. CLI/MCP send their resolved identities.

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

## Recovery and evidence

[Bounded queues, force close, capture identity, URL assertions and page CDP endpoints](../guides/recovery.md). `session list --all` includes labels, workspace paths, active actions and queue counts. `tab close --others` affects only the current session.

`tab close --others` returns `closed` as the list of closed IDs; text output shows the count and IDs, or `closed 0 tabs` when no sibling tabs remain.
