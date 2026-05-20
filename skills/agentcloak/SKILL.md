---
name: agentcloak
description: "Browser automation via cloak CLI. Navigates pages with anti-bot stealth, snapshots accessibility tree with [N] element refs for interaction, takes screenshots, evaluates JS, fetches HTTP with cookies, captures network traffic, manages profiles/tabs. Use this skill whenever the task involves ANY web page interaction: opening URLs, reading page content, filling forms, clicking buttons, taking screenshots, extracting data from websites, logging into sites, checking what a page shows, scraping, or automating browser workflows. Also use when the user mentions a URL and wants to see or interact with its content, even if they don't say 'browser'. This skill provides stealth bypass for anti-bot protections."
---

# agentcloak

Stealth browser automation for AI agents. Daemon auto-starts on first command.

Use `cloak` (short for `agentcloak`). High-frequency commands have top-level shortcuts -- `cloak navigate`, `cloak snapshot`, `cloak click` etc.

First-time setup: read `references/getting-started.md`.

## Core Workflow

Observe-then-act. Snapshot first because `[N]` refs are only valid for the current page state.

1. **Navigate**: `cloak navigate "https://example.com" --snap` -- navigate and get snapshot in one step
2. **Observe**: `cloak snapshot` -- get a11y tree with `[N]` element refs (or use `--snap` on navigate/action)
3. **Act**: `cloak click 5` or `cloak fill 3 "query"` (positional `[N]` is shorter than `--index N`)
4. **Handle feedback**: actions print proactive state inline after the confirmation line — `pending_requests`, `dialog`, `navigation`, `download`, `current_value` — whenever relevant. When `Error: blocked by dialog` shows on stderr, run `cloak dialog accept/dismiss` before retrying.
5. **Re-observe if needed**: when navigation occurred or DOM changed, snapshot again
6. **Repeat** steps 2-5

**What you'll see** (default text mode — stdout is the data, no JSON parsing):

```text
$ cloak navigate https://example.com
https://example.com/ | Example Domain

$ cloak snapshot
# Example Domain | https://example.com/ | 8 nodes (1 interactive) | seq=2
  heading "Example Domain" level=1
  paragraph "This domain is for use in illustrative examples in documents."
  [1] link "Learn more" href="https://iana.org/domains/example"

$ cloak click 1
clicked [1]
  navigation: https://iana.org/...

$ cloak js evaluate "document.title"
Example Domain
```

## Element Addressing

`cloak snapshot` returns an indented a11y tree with `[N]` indexed elements:

```
navigation "Main Nav"
  [1] link "Home"
  [2] link "Shop"
  [3] textbox "Search" value="shoes" focused
main "Content"
  [4] link "Item 1 - $29.99"
  [5] button "Add to cart"
form "Login"
  [6] textbox "Email" required
  [7] textbox "Password" value="••••" required
  [8] button "Submit"
```

Numbers are element references — pass them as the first positional arg (`cloak click 5`) or via `--index 5`. They change on navigation/DOM update -- always re-snapshot for fresh refs. ARIA states shown: `checked`, `disabled`, `expanded`, `selected`, `pressed`, `invalid`, `required`, `focused`. Passwords redacted as `••••`.

Snapshot modes: `compact` (default, interactive + containers only, capped at 80 nodes — pass `--limit 0` to disable the cap) | `accessible` (full tree, heavier) | `content` (text from the a11y tree) | `dom` (raw HTML).

## Command Reference

### Navigation & Observation

| Command | Purpose |
|---------|---------|
| `cloak navigate URL` | Navigate to URL (add `--snap` to get a11y tree in one step) |
| `cloak snapshot` | Get a11y tree with `[N]` refs (default mode: compact) |
| `cloak snapshot --mode accessible` | Full a11y tree (heavier, all containers) |
| `cloak snapshot --mode content` | Text extraction |
| `cloak snapshot --limit 50` | Limit node count (summary of hidden); `--max-nodes` still accepted |
| `cloak snapshot --focus N` | Expand subtree around element [N] |
| `cloak snapshot --offset 50` | Paginate from 50th element |
| `cloak snapshot --frames` | Include iframe content |
| `cloak snapshot --diff` | Mark `[+]` added, `[~]` changed vs previous |
| `cloak screenshot [--output FILE]` | Screenshot to file, stdout = path (`--full-page`, `--format jpeg\|png`, `--quality N`; see `recipes.md` for format choice) |
| `cloak resume` | Session state: URL, tabs, recent actions |

### Interaction

Actions accept the element index positionally (`cloak click 5`) or via `--index N`. Most also take a positional second arg where it makes sense (`cloak fill 3 "query"`).

| Command | Purpose |
|---------|---------|
| `cloak click N` | Click element (`--x/--y` coordinate fallback when no `[N]` ref; `--button right`; `--click-count 2` for double-click) |
| `cloak fill N "value"` | Clear and set input value — fast path, but slow under humanize (see Gotchas) |
| `cloak type N "value"` | Type character by character; pick this when you want the anti-detection typing cadence |
| `cloak press Enter` | Press key (Enter, Tab, Escape, Backspace, ArrowDown, Space...; `--target N` focuses element [N] first) |
| `cloak press "Control+a"` | Combo key (Playwright `+` syntax) |
| `cloak scroll down` | Scroll page (`--amount N` pixels, default 300; `--index N` scrolls element into view) |
| `cloak hover N` | Hover over element |
| `cloak select N --value "opt"` | Select dropdown option (`--label "text"` to match by visible text) |
| `cloak keydown/keyup Shift` | Hold/release key |
| `cloak dialog accept` / `dismiss` | Handle confirm/prompt dialog |
| `cloak wait --selector ".results"` | Wait for element / URL / JS condition / time |
| `cloak upload --index N --file path` | Upload file to input element |
| `cloak frame focus --name "x"` | Switch to iframe (`--main` to return) |
| `--snap` (flag on any action) | Attach a compact snapshot to the result — see Smart Behaviors |

### Content & Network

| Command | Purpose |
|---------|---------|
| `cloak js evaluate "expression"` | Execute JS in page |
| `cloak fetch URL` | HTTP GET with browser cookies |
| `cloak fetch URL --method POST --body '{...}'` | HTTP POST with cookies |
| `cloak network --since N` | Recent network requests (filter by seq; `--since last_action` returns only requests after the most recent action) |
| `cloak capture start` / `stop` / `export` | Record and export network traffic |
| `cloak console show [--level error] [--since N]` | Read captured console logs + uncaught page errors (`--clear` empties the buffer) |
| `cloak storage get [KEY]` / `set KEY VAL` / `delete KEY` / `clear` | localStorage CRUD (`--type session` for sessionStorage) |
| `cloak clipboard read` / `write TEXT` | Read/write the system clipboard |
| `cloak download url URL [-o dir]` | Download a URL server-side (with cookies; SSRF-guarded) |
| `cloak download wait [-o dir]` | Capture the next click-triggered download |
| `cloak download list` | List files downloaded this session |

### Reverse Engineering

| Command | Purpose |
|---------|---------|
| `cloak script add JS` / `add --preset fetch\|xhr\|json_parse\|crypto\|timing` | Inject an init script that runs before page scripts (the hook point for patching fetch/XHR/JSON.parse); presets log calls to `cloak console` |
| `cloak script remove ID` / `list` | Remove an init script by identifier / list active ones |
| `cloak route add PATTERN --action abort\|fulfill\|continue` | Intercept matching requests; `--status`/`--content-type`/`--body` shape a `fulfill` response, `--method`/`--resource-type` narrow the match |
| `cloak route remove [PATTERN]` / `list` | Remove a route rule (omit PATTERN to clear all) / list active rules |
| `cloak emulation headers -H 'Name: value'` | Inject extra HTTP headers on every request (forged auth/tokens); no `-H` clears them |
| `cloak graphql introspect URL` | Run the standard `__schema` introspection query (via the session's cookies) |
| `cloak graphql query URL QUERY [--variables '{...}']` | Send an arbitrary GraphQL operation |
| `cloak ws list` | List tracked WebSocket connections (capture turns on lazily; cleared on navigation) |
| `cloak ws messages [--since N]` | Read buffered WebSocket frames (→ sent, ← received); page with `--since` like `console` |
| `cloak sse messages [--since N]` | Read buffered Server-Sent Events; page with `--since` |

### Management

| Command | Purpose |
|---------|---------|
| `cloak launch --tier cloak\|playwright\|remote_bridge` | Hot-switch the daemon's browser tier (no restart) |
| `cloak profile list` / `create` / `launch` / `delete` | Browser profile management (`create --from-current` snapshots the live session's cookies) |
| `cloak tab list` / `new` / `close` / `switch` | Tab management |
| `cloak spell list` / `info` / `run NAME` / `scaffold` | Spells (reusable site automation) |
| `cloak cookies export [--url URL]` / `import -c '[...]'` | Export/import cookies (text output is `domain \| name=value`; pass `--url` to scope to one site — without it every site's cookies are returned, with import preserving httpOnly) |
| `cloak cookies set NAME VAL [--domain D]` / `set --curl '<copy-as-curl>'` / `clear` / `delete NAME` | Cookie CRUD; `--curl` seeds cookies from a DevTools Copy-as-cURL string |
| `cloak pdf [-o file] [--format A4] [--landscape]` | Export the current page to PDF (headless only) |
| `cloak serve start DIR [--port P]` / `stop` / `status` | Local http server for previewing local files (`file://` is blocked); navigate to the printed URL |
| `cloak cdp endpoint` | Get CDP WebSocket URL (for jshookmcp) |
| `cloak config` | Show merged config with value sources (default/env/toml) |
| `cloak config get KEY` | Print one value (e.g. `cloak config get browser.proxy`) |
| `cloak config set KEY VAL [K2 V2 ...]` | Set scalar(s) or replace a list (batch supported) |
| `cloak config add KEY VAL ...` | Append values to a list-typed key (e.g. `browser.extra_args`) |
| `cloak config remove KEY VAL` | Remove one value from a list-typed key |
| `cloak config unset KEY` | Clear a key so it falls back to its default |
| `cloak config keys` | List every settable dot-notation key |
| `cloak version` | Show agentcloak version (same value as `cloak --version`) |
| `cloak doctor` | Self-check diagnostics (`--detail` for one line per probe; default is a 2-line summary + runtime status) |
| `cloak skill install` / `update` / `uninstall` | Install this skill bundle into an agent platform (`--platform claude\|codex\|cursor\|opencode\|all`, `--path DIR`; see `getting-started.md`) |
| `cloak bridge start` / `claim` / `finalize` / `doctor` | RemoteBridge (real browser); `bridge doctor` checks the extension + WS toolchain |
| `cloak bridge token` / `--reset` | Show or rotate the persistent bridge auth token |

## Response Convention

CLI is **text-first**: stdout is the answer (no JSON parsing required), hints/warnings/errors go to stderr, and `$?` is `0` success / `1` failure / `2` bad usage. Errors carry a recovery hint:

```text
$ cloak click 99
Error: Element [99] not in selector_map (4 entries)
  -> run 'snapshot' to refresh the selector_map, or re-snapshot if the page changed
```

For `jq` scripting, `--json` (or `AGENTCLOAK_OUTPUT=json`) restores the legacy envelope — shape and MCP rendering notes in `references/troubleshooting.md`.

## Smart Behaviors

These work automatically:
- **Stale ref auto-retry**: `element_not_found` triggers one automatic re-snapshot + retry
- **`--snap`**: add to `navigate` or any action to get a compact snapshot back in the same call (output starts with `# Title | url | N nodes`), saving a round trip
- **`$N.path` batch refs**: in `--calls-file` batch mode, reference prior results (e.g. `"$0.url"`)
- **Tab group**: RemoteBridge auto-groups agent tabs under blue "agentcloak" Chrome tab group

## Common Patterns

**Wait before screenshot** (fonts, SPA data, dynamic content settle before the capture):

```bash
cloak wait --load networkidle
cloak wait --js "document.fonts.ready.then(() => true)"
cloak screenshot --format png
```

**Screenshot format**: `jpeg` (default, ~4-10x smaller — observe-act loops, layout checks) vs `png` (lossless — UI design, OCR, vision models). MCP defaults to quality 50.

**Wait variants**: `--selector ".el"` | `--url "**/path"` | `--load networkidle` | `--js "expr"` | `--ms N` | add `--state hidden` to wait for disappearance. `--js` must return a truthy value — wrap Promises: `.then(() => true)`.

**Coordinate fallback**: when an element has no `[N]` ref, click by position — `cloak click --x 150 --y 300`.

More multi-step recipes in `references/recipes.md`.

## Gotchas

Counter-intuitive behaviors worth knowing before you hit them:

- **`fill` is slow under humanize**: humanize is on by default, so CloakBrowser intercepts `fill` and replays it as click + select-all + character-by-character typing (~3s for a 30-char field). For bulk speed disable humanize globally (`AGENTCLOAK_HUMANIZE=false` or `cloak daemon start --no-humanize`); there's no per-action flag — switching needs a daemon restart.
- **`[N]` refs expire**: they're only valid for the current page state and change on navigation/DOM update — re-snapshot for fresh refs.
- **Truncated refs still work**: compact mode caps printed output at 80 nodes, but you can still `cloak click N` on a ref that was truncated from the tree — the daemon keeps the full mapping.
- **`--js` needs truthy**: a `wait --js` expression that returns `undefined`/`false` never satisfies. Wrap Promises with `.then(() => true)`.
- **`networkidle` can hang**: on long-polling / streaming pages `cloak wait --load networkidle` may never settle — prefer `--selector` or `--js` there.

## Key Principles

- **Timeouts**: navigation and actions both default to 30s. For slow pages or large uploads, pass `--timeout 60` on `navigate` or `wait`. If `navigation_timeout` errors persist, set `AGENTCLOAK_NAVIGATION_TIMEOUT=60` globally
- **Headless by default**: the browser runs headless. For stronger anti-detection, start headed without changing config: `cloak daemon stop && cloak daemon start --headed -b`. Or set `headless = false` in `~/.agentcloak/config.toml` (or `AGENTCLOAK_HEADLESS=false`). Xvfb auto-starts on headless Linux servers
- **Daemon lifecycle**: auto-starts on first command, stays running. `cloak launch --tier X` hot-switches browser tier without restart. Changing headless/profile requires `cloak daemon stop` + `cloak daemon start`. `cloak daemon status` shows current state

For token-saving command choices and error-recovery sequences, read `references/optimization.md`.

## References

Read these when you need deeper guidance:

| Reference | Trigger signal — read it when… |
|-----------|-------------------------------|
| `references/getting-started.md` | first run, install, `cloak skill install`, config keys/env vars |
| `references/recipes.md` | you need a multi-step sequence (search, login+save, dialog, upload, iframe, large-page exploration, wait-then-screenshot) |
| `references/optimization.md` | you want the cheapest command for a goal, or an error keeps recurring and you need a recovery sequence |
| `references/data-and-spells.md` | capturing traffic, running/writing spells, batch `--calls-file`, fetch with custom headers |
| `references/remote-bridge.md` | operating the user's real Chrome via the extension (token auth, claim, finalize, `bridge doctor`) |
| `references/troubleshooting.md` | you see a `Error:` on stderr and the inline hint isn't enough, or the daemon won't start; also the `--json` envelope shape |
| `references/commands-reference.md` | you need an exact daemon parameter / type — full route catalog with CLI / MCP bindings (auto-generated from the OpenAPI spec) |
