# Troubleshooting

CLI output is text-first. The useful data lands on **stdout**, hints and errors land on **stderr**, and `$?` is `0` on success, `1` on failure, `2` on bad usage. Add `--json` (or set `AGENTCLOAK_OUTPUT=json`) if you want structured errors (`error.code` and `error.message`) for `jq` scripting.

## Error Recovery Quick Reference

When a command exits non-zero, read the `Error [code]: …` line on stderr — the `-> hint` after the arrow tells you the next move.

```text
$ cloak click 99
Error [element_not_found]: Element [99] not in selector_map (4 entries)
  -> run 'snapshot' to refresh the selector_map, or re-snapshot if the page changed
```

| Error text on stderr | Cause | Recovery |
|----------------------|-------|----------|
| `invalid_request` / `command_failed` | Invalid arguments or local validation | Read `error.message` and check the command help |
| `command_aborted` | Interrupted command | Retry when ready |
| `internal_error` | Unexpected exception | Read the message and check daemon logs |
| `config_error` | Invalid config key/value | Run `cloak config list` |
| `doctor_failed` / `bridge_check_failed` | Diagnostic checks failed | Inspect JSON `data.checks` and follow the failing check's hint |
| `skill_uninstall_failed` | File access/removal failed | Fix the reported path permissions and retry |
| `daemon_invalid_response` / `daemon_request_failed` | Invalid body or failed daemon request | Check daemon logs and the selected endpoint |
| `element_not_found` / `[N] not in selector_map` | `[N]` ref is stale (page changed) | Auto-retried once; if still fails, re-snapshot and use the new ref |
| `element_covered` / visible ref does not react | Overlay intercepted coordinate click | Hide the overlay with `cloak hide add CSS`, then re-snapshot; use `--force` only as a one-off single-left-click fallback |
| `navigation_timeout` | Page took too long to load; local backends stop the abandoned navigation | Retry with `--timeout 60`, or check the URL is correct |
| `session_busy` | Session queue wait exceeded `browser.action_timeout` | Inspect `session list --all` for active actions/queue; `session close --force` cancels work and closes only the selected session |
| `action_timeout` | Page operation exceeded its execution budget | Navigate again; if the renderer is frozen, close the session with `--force` first |
| `page_recreated` / `page_lost` | Page or browser disappeared | Navigate to the intended URL before collecting evidence; blank replacement pages are not valid evidence |
| `url_mismatch` / `page_changed` | Redirect or navigation invalidated capture identity | Check login and final URL, then retry with the expected URL/path |
| `no_valid_page` | Last `navigate` failed — page is still the previous URL | `cloak navigate <url>` again before screenshot/snapshot/click/evaluate. `fetch` and `network` are unaffected |
| `blocked_by_dialog` | A dialog is blocking operations | `cloak dialog accept` or `dismiss`, then retry the action |
| `debugger_paused` | Execution is paused at a breakpoint — page actions can't run | `cloak debugger resume` or `debugger step`, then retry. `debugger`/`console`/`tab` commands stay available while paused |
| `wait_timeout` | Wait condition not met in time | Increase `--timeout` (`screenshot --wait-for`: `--wait-timeout`), or verify selector/condition; screenshot does not write a file after this failure |
| `evaluate_failed` | JavaScript syntax/runtime failure | Read the bounded exception message and first source location; fix the script or probe nullable elements with optional chaining |
| `cdp_timeout` / `cdp_call_failed` | Raw CDP exceeded its request budget or failed at protocol level | Fix method/params or raise `--timeout`; after timeout reapply raw CDP settings because its channel was reset |
| `frame_not_found` | Frame name/URL doesn't match | `cloak frame list` to see available frames |
| `daemon_not_running` / `daemon_unreachable` | Daemon crashed or wasn't started | Should auto-start; if not, run `cloak daemon start -b` (or `cloak doctor --fix`) |
| `daemon_auto_start_failed` | Daemon couldn't come up on first command | `cloak doctor --fix` — its in-process diagnosis reports what's missing |
| `daemon_timeout` | Daemon is up but slow to respond | Increase `AGENTCLOAK_HTTP_CLIENT_TIMEOUT` |
| `stealth_not_installed` | The cloakbrowser pip dep didn't install correctly | `pip install agentcloak --upgrade` (or `cloak doctor --fix`) |
| `xvfb_not_found` | Headed mode on a headless Linux box without Xvfb | Install Xvfb (doctor prints the per-distro command) or set `headless = true` |
| `spell_no_browser` | Browser spell reached an executor without daemon context | Run it through `cloak spell run`; if it persists, use `cloak doctor --fix` and retry |
| `spell_no_handler` | Spell definition is broken | Check the spell code |
| `outbound_target_blocked` | fetch/download URL resolves to a private/loopback/link-local IP (SSRF guard) | Use a public URL; this guard protects against prompt-injection SSRF |
| `outbound_scheme_blocked` | fetch/download URL uses a non-http(s) scheme | Use http:// or https:// |
| `batch_step_failed` | A step within `action_batch` threw an unexpected error | Check the `hint` field for details; `step_index` and `kind` identify the failing step |

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

When an action errors with `blocked_by_dialog`, the CLI line only names the code and the recovery action. Run `cloak dialog status` to read the dialog `type`/`message` (the daemon's raw HTTP envelope also carries them under `dialog`).

## Daemon Issues

Clients discover the daemon through `/health` and never delete `daemon.json`. Sandboxed callers do not need to see the host PID or write the state directory. A closed local page/browser is recreated on the next request; re-navigate if the browser lost its page state.


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

### Overlay blocks observation or clicks

Prefer the hide layers because one selector fixes snapshots, screenshots, and click hit-testing together:

```bash
cloak hide add ".feedback-toolbar"       # persists with an active profile
cloak hide list                           # shows builtin/profile/session scope
cloak snapshot                            # overlay is absent and refs are refreshed
cloak click N                             # normal hit-testing now reaches the target
```

Use `--hide ".feedback-toolbar,.toast"` for one snapshot or screenshot. A page can mark its own automation-only UI with `[data-cloak-hide]`. Use `--keep-overlays` when the real obstruction must remain visible. `cloak click N --force` is the fallback for an unknown one-off overlay and only supports a single left click; combining it with a non-default button or click count returns `invalid_argument`.

**Portal gotcha:** React portal components (modals, tooltips, floating toolbars) render outside their parent's DOM subtree — typically directly on `<body>`. A `[data-cloak-hide]` attribute on the component root won't cover portal-rendered DOM. Add selectors targeting the actual rendered elements (e.g. `[class*=toolbar]`, `[data-feedback-toolbar]`) via `cloak hide add`.

### Login state disappeared

`cookies export` without `--output` refreshes the active profile's `cookies-snapshot.json` (or `~/.agentcloak/cookies-snapshot.json` without a profile). Restore it, then reload the page:

```bash
cloak cookies restore
cloak press Control+r
```

Use `cloak cookies restore --file /path/to/snapshot.json` for an explicit snapshot. Import and restore accept Chrome cookies API, CDP, and Playwright cookie shapes; malformed entries are skipped and reported instead of aborting the whole import.

### Too much output

`cloak snapshot` already runs `--mode compact` with a default 80-node cap. If the relevant region has a stable main-document selector, scope first with `--within main`; otherwise tighten with `--limit 50` or zoom into an area with `--focus N` (expand around `[N]`). Pass `--limit 0` to disable the cap entirely.

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
{"ok": false, "error": {"code": "element_not_found", "message": "..."}, "hint": "...", "action": "..."}
```

CLI failures, including argument errors, exit nonzero with one JSON envelope on stdout and a diagnostic on stderr. JavaScript throws and rejected promises fail; ordinary returned strings containing `Error:` do not.

MCP tools return the same human-readable text the CLI prints (rendered locally via `core/text_renderers`). `agentcloak_screenshot` returns `ImageContent` for multimodal LLMs. Direct daemon responses and MCP errors keep the existing string `error` plus `hint` and `action`; CLI JSON exposes the code and explanation as `error.code` and `error.message`.

## Session recovery and diagnostics

### Stuck or frozen session

`session list --all` shows labels, workspace paths, active actions and queued counts for every workspace. On local backends `session close --force` bypasses the queue, cancels that session's requests and terminates frozen page execution before closing its tabs; other sessions and workspace storage stay alive. Force recovery skips renderer-dependent persistence, while normal workspace shutdown still persists storage. Disconnecting an HTTP client cancels its request on the daemon. RemoteBridge returns `force_recovery_unavailable` — use its normal release/reconnect lifecycle.

Session queues and tab ownership do not isolate shared browser resources. Same-origin sibling sessions may stall together when long-lived HTTP/1.x streams (SSE) exhaust Chromium's per-destination connection pool, or when a shared renderer stalls; closing the offending session can release its siblings. A responsive `curl` does not rule out browser pool exhaustion — inspect the active streams and NetLog before blaming CDP. Fix duplicate subscriptions or serve the stream over browser-facing HTTP/2 in the application; agentcloak does not automatically terminate application streams to relieve connection-pool exhaustion. Workspace storage isolation is not a per-session browser-process guarantee.

### Diagnostics

Start with `cloak daemon start --log-level info` for request entered/acquired/started/finished timestamps and session IDs. On Unix, `kill -USR1 <daemon-pid>` writes asyncio task stacks to the daemon log without ptrace. `cloak version --json`, `/health` and `daemon.json` expose `build_id`: a source-content fingerprint that also works for installed wheels; a Git checkout additionally reports its commit. `--version` prints both release and build identity.

`cdp endpoint --page` returns the exact calling session's page target on local backends. It is not a security boundary: browser-level CDP permissions still reach other targets.

### One daemon per state directory

The daemon holds `daemon.lock` for its lifetime; a duplicate start fails with `daemon_already_running` without replacing runtime records. For an isolated daemon set `AGENTCLOAK_HOME` *and* a distinct `AGENTCLOAK_PORT`. Clients probe `/health` and take the active profile from that live response, so PID visibility is not required. Restart after upgrading to activate the lock.

### Popups and sibling tabs

Actions and evaluations report new popups via `new_tab`; a slow popup may initially show `pending: true` without a tab ID, so run `tab list` before targeting it. `tab close --others` closes only sibling tabs in the current session; `closed` lists their IDs, or is empty when nothing remains to close.

### Pointer emulation rejected

`unsupported_operation` from `emulate --pointer` means the backend cannot restore pointer state reliably. Use a local headed browser (`browser.headless=false`, with Xvfb on a server). Color scheme, reduced motion and DPR remain available in headless local browsers. RemoteBridge does not support session environment changes.

### Profile localStorage looks stale

Profile localStorage is native browser state: navigation never replays `localStorage-snapshot.json`. `profile create --from-current` seeds a new native profile once. If a legacy profile contains only a snapshot, recreate it from a live authenticated session; do not overwrite current storage with an old backup.

### Pending requests, batches and closed sessions

`network --pending` keeps live SSE/EventSource streams but retires requests when their document is replaced or their frame detaches; history/hash navigation keeps them. In `do batch`, only the first snapshot after actions waits (up to `browser.batch_settle_timeout`) for requests those actions started; read-only batches never wait, and SSE never blocks settling — use an explicit selector/JS wait for application readiness. A session listed as `suspended` retains only its identity: the next request creates a fresh `about:blank`, where storage access returns `storage_origin_error` until you navigate to the target origin.

### `evaluate_failed` during navigation

On local backends this can mean the selected main-document context was destroyed. The script is not replayed and no child-frame fallback is used; check the resulting page state before retrying a script with side effects. `frame focus` does not retarget page-level evaluation.
