# Troubleshooting

CLI output is text-first. The useful data lands on **stdout**, hints and errors land on **stderr**, and `$?` is `0` on success, `1` on failure, `2` on bad usage. Add `--json` (or set `AGENTCLOAK_OUTPUT=json`) if you want the legacy envelope shape for `jq` scripting.

## Error Recovery Quick Reference

When a command exits non-zero, read the `Error: …` line on stderr — the `-> hint` after the arrow tells you the next move.

```text
$ cloak click 99
Error: Element [99] not in selector_map (4 entries)
  -> run 'snapshot' to refresh the selector_map, or re-snapshot if the page changed
```

| Error text on stderr | Cause | Recovery |
|----------------------|-------|----------|
| `element_not_found` / `[N] not in selector_map` | `[N]` ref is stale (page changed) | Auto-retried once; if still fails, re-snapshot and use the new ref |
| `navigation_timeout` | Page took too long to load | Retry with `--timeout 60`, or check the URL is correct |
| `no_valid_page` | Last `navigate` failed — page is still the previous URL | `cloak navigate <url>` again before screenshot/snapshot/click/evaluate. `fetch` and `network` are unaffected |
| `blocked_by_dialog` | A dialog is blocking operations | `cloak dialog accept` or `dismiss`, then retry the action |
| `wait_timeout` | Wait condition not met in time | Increase `--timeout`, or verify selector/condition |
| `frame_not_found` | Frame name/URL doesn't match | `cloak frame list` to see available frames |
| `daemon_not_running` / `daemon_unreachable` | Daemon crashed or wasn't started | Should auto-start; if not, run `cloak daemon start -b` (or `cloak doctor --fix`) |
| `daemon_auto_start_failed` | Daemon couldn't come up on first command | `cloak doctor --fix` — its in-process diagnosis reports what's missing |
| `daemon_timeout` | Daemon is up but slow to respond | Increase `AGENTCLOAK_HTTP_CLIENT_TIMEOUT` |
| `stealth_not_installed` | The cloakbrowser pip dep didn't install correctly | `pip install agentcloak --upgrade` (or `cloak doctor --fix`) |
| `xvfb_not_found` | Headed mode on a headless Linux box without Xvfb | Install Xvfb (doctor prints the per-distro command) or set `headless = true` |
| `spell_no_browser` | Spell needs browser but none launched | Navigate to a page first, then run the spell |
| `spell_no_handler` | Spell definition is broken | Check the spell code |

## Dialog Handling

Dialogs (alert, confirm, prompt, beforeunload) block all browser operations.

- **alert / beforeunload**: auto-accepted, you'll see them in action feedback as `auto_dialog`
- **confirm / prompt**: stored as pending, you must handle explicitly

```bash
cloak dialog status                 # check for a pending dialog
cloak dialog accept                 # OK / confirm
cloak dialog accept --text "reply"  # answer a prompt dialog
cloak dialog dismiss                # Cancel
```

When any action errors with `blocked_by_dialog`, the stderr line includes the dialog type and message — you already know what it says without calling `dialog status`.

## Daemon Issues

### Daemon won't start

```bash
cloak doctor           # comprehensive self-check
cloak daemon status    # quick status check
```

Common causes:
- Port conflict: daemon tries 18765-18774, all busy → check for zombie processes
- Browser binary missing: `cloak doctor` will say so (`cloak doctor --fix` downloads it)
- Headed mode on a headless Linux box without Xvfb installed

### Daemon auto-start

The daemon starts on your first command and stops after idle timeout. You rarely need manual control:

```bash
cloak daemon start -b    # manual start (background)
cloak daemon stop        # manual stop
cloak daemon status      # tier, browser status, seq, current URL, capture state
```

## Snapshot Issues

### Too much output

`cloak snapshot` already runs `--mode compact` with a default 80-node cap. Tighten with `--limit 50`, or zoom into an area with `--focus N` (expand around `[N]`). Pass `--limit 0` to disable the cap entirely.

### Missing elements

Some elements may live in iframes. Try `--frames` to include iframe content. Or `--mode dom` for raw HTML (large output, last resort).

### Stale refs

If `element_not_found` persists after the automatic retry, the page has changed significantly. Take a fresh `cloak snapshot` and find the element again.

## RemoteBridge Issues

### Extension not connecting

1. Check the extension is loaded: `chrome://extensions` → agentcloak should show "Active"
2. Check the daemon is running: `cloak daemon status`
3. Check ports: the extension probes 18765-18774. If the daemon is on a different port the extension can't find it
4. Check network: the extension connects via WebSocket to the daemon's IP

### CDP endpoint not available

```bash
cloak cdp endpoint
# stderr: "browser may not have CDP port exposed"
# CloakBrowser backend always has CDP; RemoteBridge gets it from the extension
```

## Performance Tips

- Use `--mode compact` (the default) instead of full `accessible` when you only need interactive elements
- Add `--snap` to actions to save a round trip (output gets a `# Title | url | N nodes` header plus the compact tree)
- Use `--diff` to only see changes since the last snapshot
- Use batch mode (`cloak do batch --calls-file`) for multiple sequential actions
- Use `--max-chars` to limit output size when working with token budgets

## JSON Mode (Scripting)

For `jq` pipelines or backwards-compatible automation, add `--json`:

```bash
cloak --json snapshot | jq -r '.data.tree_text'
AGENTCLOAK_OUTPUT=json cloak click 5 | jq '.ok'
```

JSON envelope shape:

```json
{"ok": true, "seq": 3, "data": {"...": "..."}}
{"ok": false, "error": "element_not_found", "hint": "...", "action": "..."}
```

MCP tools return the same human-readable text the CLI prints (rendered locally via `core/text_renderers`). `agentcloak_screenshot` returns `ImageContent` for multimodal LLMs. Errors stay as the three-field JSON envelope so failure handling matches the CLI `--json` contract.
