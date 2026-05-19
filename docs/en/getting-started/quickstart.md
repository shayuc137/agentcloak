# Quick start

This tutorial walks through the core agentcloak workflow: navigate to a page, read it through an accessibility tree snapshot, interact with elements, and verify results.

## The observe-act loop

agentcloak works on a simple cycle:

1. **Navigate** to a page
2. **Snapshot** to see the accessibility tree with `[N]` element references
3. **Act** on elements using their `[N]` reference numbers
4. **Re-snapshot** after actions (references change when the page updates)

## First run

The daemon starts automatically on the first command. No setup step needed.

```bash
# Navigate and get a snapshot in one step
cloak navigate "https://example.com" --snap
```

stdout becomes the answer directly — one line for the navigation, then the snapshot tree:

```text
https://example.com/ | Example Domain

# Example Domain | https://example.com/ | 8 nodes (1 interactive) | seq=1
  heading "Example Domain" level=1
  paragraph "This domain is for use in illustrative examples in documents."
  [1] link "Learn more" href="https://iana.org/domains/example"
```

## Reading the snapshot

The snapshot is an accessibility tree where each interactive element gets a `[N]` reference:

```text
# Example Domain | https://example.com/ | 8 nodes (1 interactive) | seq=1
  heading "Example Domain" level=1
  paragraph "This domain is for use in illustrative examples in documents."
  [1] link "Learn more" href="https://iana.org/domains/example"
```

- The header line carries the page title, URL, node counts, and the daemon `seq` (monotonic state counter).
- Plain rows are containers / context elements.
- `[N]` rows are interactive — use the number as the click/fill target.
- Inputs show their current value; password fields are redacted as `••••`.

### Snapshot modes

| Mode | What it shows | When to use |
|------|--------------|-------------|
| `compact` | Interactive elements + named containers only | Default — token-efficient |
| `accessible` | Full a11y tree with `[N]` refs, ARIA states, values | When you need every container / heading |
| `content` | Text extraction | Reading articles or text-heavy pages |
| `dom` | Raw HTML | Debugging or CSS selector work |

```bash
cloak snapshot --mode accessible     # full a11y tree
cloak snapshot --mode content        # text extraction
```

## Interacting with elements

Use `[N]` references from the snapshot as action targets. Elements accept the index positionally (shorter for agents) or via `--index N`:

```bash
# Click a link (positional or --index both work)
cloak click 1

# Fill a text field
cloak fill 5 "search query"

# Press a key
cloak press Enter

# Select a dropdown option
cloak select 8 --value "option-2"
```

Each action prints a confirmation line and any proactive feedback:

```text
$ cloak click 1
clicked [1]
  navigation: https://www.iana.org/domains/example
```

### Getting post-action state

Add `--snap` to any action to get a snapshot in the same response — saves a round-trip vs running `cloak snapshot` separately:

```bash
cloak click 2 --snap
```

```text
clicked [2]
  navigation: https://example.com/page2

# Page Two | https://example.com/page2 | 12 nodes (4 interactive) | seq=4
  ...
```

## Complete login example

```bash
# Navigate to login page and get snapshot
cloak navigate "https://example.com/login" --snap
# Snapshot output (excerpt):
# heading "Sign In" level=1
# [1] textbox "Email"
# [2] textbox "Password" value="••••"
# [3] button "Sign In"

# Fill credentials
cloak fill 1 "user@example.com"
cloak fill 2 "my-password"

# Submit and get new snapshot
cloak click 3 --snap

# Save login state for reuse
cloak profile create my-session
```

Next time, launch with the saved profile:

```bash
cloak daemon start --profile my-session
cloak navigate "https://example.com/dashboard" --snap
```

## Network monitoring

Check what network requests a page makes:

```bash
# See recent requests
cloak network requests

# See only requests since the last action
cloak network requests --since last_action
```

## Taking screenshots

```bash
# Viewport screenshot (JPEG, ~75-85% smaller than PNG)
cloak screenshot
# stdout = file path in the OS temp dir, e.g.
#   Linux/macOS: /tmp/agentcloak-1715920000.jpg
#   Windows:     C:\Users\you\AppData\Local\Temp\agentcloak-1715920000.jpg

# Full scrollable page
cloak screenshot --full-page

# PNG for pixel-perfect fidelity
cloak screenshot --format png

# Save to a specific file
cloak screenshot --output page.png
```

## Working with large pages

For pages with many elements, use progressive loading:

```bash
# Limit output to 80 nodes (--max-nodes also accepted)
cloak snapshot --limit 80

# Paginate through results
cloak snapshot --offset 80 --limit 80

# Focus on a specific element's subtree
cloak snapshot --focus 15

# See what changed after an action
cloak snapshot --diff
```

## Capturing API traffic

Record and analyze network traffic to discover API patterns:

```bash
cloak capture start
cloak navigate "https://api-heavy-site.com"
# interact with the page...
cloak capture stop

# Export as HAR (raw bytes to stdout — pipe to a file)
cloak capture export --format har > traffic.har

# Auto-detect API patterns
cloak capture analyze
```

See the [capture guide](../guides/capture.md) for more on API analysis and spell generation.

## Output format

CLI is text-first **stdout is the answer.** Hints / errors go to stderr; exit code is 0 on success, 1 on failure, 2 on bad usage.

```text
$ cloak navigate https://example.com
https://example.com/ | Example Domain

$ cloak click 99
Error: Element [99] not in selector_map (1 entries)
  -> run 'snapshot' to refresh the selector_map, or re-snapshot if the page changed
```

Need the legacy JSON envelope (for `jq` pipelines or other structured consumers)? Pass `--json`, or set `AGENTCLOAK_OUTPUT=json`:

```bash
cloak --json snapshot | jq -r '.data.tree_text'
AGENTCLOAK_OUTPUT=json cloak snapshot
```

JSON shape when `--json` is active:

```json
{"ok": true, "seq": 3, "data": {"url": "https://example.com", "title": "Example"}}
{"ok": false, "error": "element_not_found", "hint": "No element at index 99", "action": "re-snapshot to get fresh [N] refs"}
```

`seq` is a monotonic counter that increments on every browser state change.

## Next steps

- [CLI reference](../reference/cli.md) -- all commands and flags
- [Browser backends](../guides/backends.md) -- CloakBrowser vs Playwright vs RemoteBridge
- [MCP setup](../guides/mcp-setup.md) -- connect from AI clients via MCP
- [Configuration](../reference/config.md) -- customize daemon behavior
