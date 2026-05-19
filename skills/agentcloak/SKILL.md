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
4. **Handle feedback**: action stdout shows proactive state (navigation, pending_requests, dialog)
5. **Re-observe if needed**: when navigation occurred or DOM changed, snapshot again
6. **Repeat** steps 2-5

Actions emit `pending_requests`, `dialog`, `navigation`, `download`, `current_value` inline after the confirmation line when relevant. When `Error: blocked by dialog` appears on stderr, handle with `cloak dialog accept/dismiss` before retrying.

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
| `cloak screenshot` | Take page screenshot (saves file, stdout = path) |
| `cloak resume` | Session state: URL, tabs, recent actions |

### Interaction

Actions accept the element index positionally (`cloak click 5`) or via `--index N`. Most also take a positional second arg where it makes sense (`cloak fill 3 "query"`).

| Command | Purpose |
|---------|---------|
| `cloak click N` | Click element |
| `cloak fill N "value"` | Clear and set input value (fast when `humanize=false`; CloakBrowser intercepts under `humanize=true` and replays as click + select-all + character-by-character typing — multi-second per field) |
| `cloak type N "value"` | Type character by character (always uses humanize timing when enabled; pick this when you need anti-detection cadence) |
| `cloak press Enter` | Press key (Enter, Tab, Escape, Backspace, ArrowDown, Space...) |
| `cloak press "Control+a"` | Combo key (Playwright `+` syntax) |
| `cloak scroll down` | Scroll page |
| `cloak hover N` | Hover over element |
| `cloak select N --value "opt"` | Select dropdown option |
| `cloak keydown/keyup Shift` | Hold/release key |
| `cloak dialog accept` / `dismiss` | Handle confirm/prompt dialog |
| `cloak wait --selector ".results"` | Wait for element / URL / JS condition / time |
| `cloak upload --index N --file path` | Upload file to input element |
| `cloak frame focus --name "x"` | Switch to iframe (`--main` to return) |
| Add `--snap` to any action | Get compact snapshot with result (saves a round trip) |

### Content & Network

| Command | Purpose |
|---------|---------|
| `cloak js evaluate "expression"` | Execute JS in page |
| `cloak fetch URL` | HTTP GET with browser cookies |
| `cloak fetch URL --method POST --body '{...}'` | HTTP POST with cookies |
| `cloak network --since N` | Recent network requests (filter by seq after `--since N`) |
| `cloak capture start` / `stop` / `export` | Record and export network traffic |

### Management

| Command | Purpose |
|---------|---------|
| `cloak launch --tier cloak\|playwright\|remote_bridge` | Hot-switch the daemon's browser tier (no restart) |
| `cloak profile list` / `create` / `launch` / `delete` | Browser profile management |
| `cloak tab list` / `new` / `close` / `switch` | Tab management |
| `cloak spell list` / `info` / `run NAME` / `scaffold` | Spells (reusable site automation) |
| `cloak cookies export [--url URL]` / `import -c '[...]'` | Export/import cookies (text output is `domain \| name=value`; pass `--url` to scope to one site — without it every site's cookies are returned, with import preserving httpOnly) |
| `cloak cdp endpoint` | Get CDP WebSocket URL (for jshookmcp) |
| `cloak config` | Show merged config with value sources (default/env/toml) |
| `cloak config get KEY` | Print one value (e.g. `cloak config get browser.proxy`) |
| `cloak config set KEY VAL [K2 V2 ...]` | Set scalar(s) or replace a list (batch supported) |
| `cloak config add KEY VAL ...` | Append values to a list-typed key (e.g. `browser.extra_args`) |
| `cloak config remove KEY VAL` | Remove one value from a list-typed key |
| `cloak config unset KEY` | Clear a key so it falls back to its default |
| `cloak config keys` | List every settable dot-notation key |
| `cloak version` | Show agentcloak version (same value as `cloak --version`) |
| `cloak doctor` | Self-check diagnostics |
| `cloak bridge start` / `claim` / `finalize` | RemoteBridge (real browser) |
| `cloak bridge token` / `--reset` | Show or rotate the persistent bridge auth token |

## Response Convention

CLI is **text-first**. stdout is the answer; no JSON parsing required.

| What you see | Where |
|--------------|-------|
| The useful data (URL, snapshot tree, JS result, ...) | stdout |
| Hints, warnings, errors | stderr |
| Exit code 0 = success, 1 = failure, 2 = bad usage | shell `$?` |

**Examples (default text mode):**

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

$ cloak doctor
all 17 checks passed | agentcloak 0.2.3
CloakBrowser 0.3.27 | headless | humanize | no proxy | no profile (ephemeral)
```

**Errors go to stderr** with a recovery hint:

```text
$ cloak click 99
Error: Element [99] not in selector_map (4 entries)
  -> run 'snapshot' to refresh the selector_map, or re-snapshot if the page changed
```

**`--json` flag** restores the full envelope for scripting / backwards compatibility:

```bash
cloak --json snapshot | jq '.data.tree_text'
# Or via env var (handy for CI / wrappers)
AGENTCLOAK_OUTPUT=json cloak snapshot | jq -r '.data.tree_text'
```

JSON envelope shape (only when `--json` is active):

```json
{"ok": true, "seq": 3, "data": {...}}
{"ok": false, "error": "element_not_found", "hint": "...", "action": "..."}
```

**MCP tools** return the same human/agent-readable text the CLI prints (rendered locally via `core/text_renderers`). `screenshot` returns an `ImageContent` so multimodal LLMs read pixels directly; errors stay as the three-field JSON envelope so failure handling matches the CLI `--json` contract.

## Smart Behaviors

These work automatically:
- **Stale ref auto-retry**: `element_not_found` triggers one automatic re-snapshot + retry
- **`--snap`**: add to any action to get a compact snapshot back, saving a round trip. Output starts with `# Title | url | N nodes`
- **`--snap` on navigate**: `cloak navigate URL --snap` returns page + a11y tree in one call
- **`$N.path` batch refs**: in `--calls-file` batch mode, reference prior results (e.g. `"$0.url"`)
- **Tab group**: RemoteBridge auto-groups agent tabs under blue "agentcloak" Chrome tab group

## Key Principles

- **Snapshot before acting**: `[N]` refs are only valid for current page state
- **Read stderr / inline feedback**: `pending_requests`, `dialog`, `navigation` lines follow the action confirmation
- **Handle dialogs immediately**: they block everything until dismissed
- **Follow error hints**: stderr `Error: ... -> action` tells you what to do next
- **Compact is the default**: `cloak snapshot` already runs in compact mode (interactive + named containers), capped at 80 nodes — pass `--limit 0` to disable the cap or `--limit 50` for a tighter budget
- **Large pages**: 100+ elements blow up token budgets. Default compact + `--limit 80` (~1.8K tokens), then `--focus N` or `--offset N` to explore specific areas. Action targets work even if truncated from the tree output
- **Timeouts**: navigation defaults to 30s, actions to 30s. For slow pages or large uploads, pass `--timeout 60` on `navigate` or `wait`. If `navigation_timeout` errors persist, set `AGENTCLOAK_NAVIGATION_TIMEOUT=60` globally
- **Headless by default**: the browser runs headless. For stronger anti-detection, start headed without changing config: `cloak daemon stop && cloak daemon start --headed -b`. Or set `headless = false` in `~/.agentcloak/config.toml` (or `AGENTCLOAK_HEADLESS=false`). Xvfb auto-starts on headless Linux servers
- **Daemon lifecycle**: auto-starts on first command, stays running. `cloak launch --tier X` hot-switches browser tier without restart. Changing headless/profile requires `cloak daemon stop` + `cloak daemon start`. `cloak daemon status` shows current state
- **`fill` vs `type` under humanize**: humanize is on by default (CloakBrowser preset `"default"`, ~70ms/char typing delay). `fill` reaches for raw `page.fill()` when humanize is off (sub-50ms write) but CloakBrowser intercepts it under humanize and replays as `click → Ctrl+A → Backspace → typed character-by-character` — a 30-char field crosses ~3 seconds. Pick `fill` for speed and disable humanize globally (`AGENTCLOAK_HUMANIZE=false` or `cloak daemon start --no-humanize`); pick `type` when the slow cadence is the point (anti-detection forms, careful preset). There's no per-action humanize flag today — switching requires a daemon restart
- **Scripting / piping**: add `--json` for the legacy envelope shape when piping to jq, or set `AGENTCLOAK_OUTPUT=json` for the same effect

## References

Read these when you need deeper guidance:

| Reference | When to read |
|-----------|-------------|
| `references/getting-started.md` | First-time setup, installation, configuration |
| `references/recipes.md` | Usage examples: search, login, dialog, upload, iframe, large pages |
| `references/data-and-spells.md` | Capture traffic, run spells, batch operations, fetch with cookies |
| `references/remote-bridge.md` | Operate user's real Chrome browser via extension |
| `references/troubleshooting.md` | Error recovery, dialog handling, daemon issues |
| `references/commands-reference.md` | Full daemon route catalog with parameters, CLI / MCP bindings (auto-generated from OpenAPI spec) |
