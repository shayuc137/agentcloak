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
cloak snapshot [--mode MODE] [--limit N] [--focus N] [--offset N] [--frames] [--diff] [--selector-map]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--mode` | `compact` | `compact` (default), `accessible`, `content`, `dom` |
| `--limit` (alias `--max-nodes`) | `0` | Truncate after N nodes (0 = no limit) |
| `--focus` | `0` | Expand subtree around element `[N]` |
| `--offset` | `0` | Start output from Nth element (pagination) |
| `--frames` | off | Include iframe content |
| `--diff` | off | Mark changes since previous snapshot |
| `--selector-map` | off | Include the raw selector_map (debug / scripting) |

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
```

### fill

Clear an input field and set its value.

```bash
cloak fill N "value" [--snap]
cloak fill --index N --text "value" [--snap]
```

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
```

Scalar results (string/number/boolean) print as raw values. Objects and arrays print as pretty JSON.

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
```

## Frame management

```bash
cloak frame list
cloak frame focus --name "frame-name"
cloak frame focus --url "partial-url"
cloak frame focus --main
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
cloak spell run NAME [--args-json '{"key": "value"}']
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
cloak daemon status                # tier | browser status | seq
```

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
