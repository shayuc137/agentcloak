# MCP tools reference

agentcloak's MCP server exposes 23 tools via stdio transport. It is included in the base install (`pip install agentcloak`) and run with `agentcloak-mcp`.

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
| `max_chars` | `int` | `0` | Truncate tree_text to N characters (0 = no limit) |
| `max_nodes` | `int` | `0` | Truncate after N nodes (0 = no limit) |
| `focus` | `int` | `0` | Expand subtree around element `[N]` |
| `offset` | `int` | `0` | Start from Nth element (pagination) |
| `frames` | `bool` | `false` | Include iframe content |
| `diff` | `bool` | `false` | Mark changes since previous snapshot |

### agentcloak_screenshot

Take a screenshot of the current page. Returns an `ImageContent` (the image bytes) plus a short `TextContent` with size/format metadata — multimodal LLMs read the pixels directly without a base64 round-trip on the agent side.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `full_page` | `bool` | `false` | Capture full scrollable page |
| `format` | `str` | `jpeg` | `jpeg` or `png` |
| `quality` | `int` | `config.mcp_screenshot_quality` | JPEG quality 0-100 (defaults lower than the CLI to fit MCP token budgets) |

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
| `js` | `str` | required | JavaScript code to evaluate |
| `world` | `str` | `main` | `main` (page globals visible) or `utility` (isolated) |
| `max_return_size` | `int` | `50000` | Max bytes of serialized result |

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

Upload files to a file input element.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `index` | `int` | required | Element `[N]` ref of file input |
| `files` | `list[str]` | required | List of absolute file paths |

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

No parameters. Returns Python version, CloakBrowser status, daemon connectivity, and configuration checks.

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
