# Recovery and trustworthy browser evidence

Each session has a bounded request queue. Queue waits use `browser.action_timeout` (milliseconds); expiry returns `session_busy` with the occupying route. Execution also has a default action-timeout budget, including snapshot and screenshot. Explicit navigation/fetch budgets use seconds and wait/CDP budgets use milliseconds; these can extend execution beyond the default. Cleanup may add a short bounded delay.

```bash
cloak session list --all
cloak session close --force
cloak navigate https://example.com/dashboard --expect-path /dashboard
cloak screenshot --expect-url 'https://example.com/dashboard*' --viewport 1280x800 -o page.png --json
cloak cdp endpoint --page
```

`session list` shows stable IDs, readable directory labels, active actions and queued counts. `--all` includes other workspaces and their canonical paths. Labels are descriptive; the hashed identity remains the namespace key. HTTP clients can send percent-encoded `X-Agentcloak-Session-Label` and `X-Agentcloak-Workspace-Path` alongside the existing workspace/session headers.

On local backends, `close --force` bypasses ordinary scheduling, cancels the selected session's requests, terminates frozen page execution and closes its tabs. It does not restart the daemon or close another session. It skips renderer-dependent storage capture and retains the workspace context; normal shutdown still persists workspace storage. A disconnected HTTP client cancels its daemon-side request. RemoteBridge force-close returns `force_recovery_unavailable`; use its normal release/reconnect lifecycle.

Unexpected page/browser loss produces `page_recreated` or `page_lost`. Navigate before collecting evidence; a replacement blank page is not silently accepted. `/health` does not build a snapshot or replace element references and bounds its best-effort page information read.

Screenshot JSON includes `url`, `title`, `viewport` (CSS width/height), `dpr`, `pixel_width` and `pixel_height` (decoded from the captured image). Temporary viewport metadata describes the capture, not the restored viewport. The output directory must exist. `--expect-url` uses case-sensitive glob matching; `navigate --expect-path` checks the exact final URL pathname, excluding query and fragment. A mismatch fails without writing a screenshot. Navigation during capture returns `page_changed`.

`cdp endpoint --page` returns the current session's exact page target URL and `target_id` on local backends. Without `--page`, it returns the browser endpoint. Selecting a page is not a CDP security boundary.

Actions and evaluations report `new_tab` and warn when many popups accumulate. A popup still loading may initially report `pending: true` without a tab ID; inspect `tab list` before targeting it. `tab close --others` closes sibling tabs only in this session. Key names and modifier aliases are case-insensitive; invalid combinations are rejected before keyboard input, and failed/cancelled presses release their newly pressed modifiers. `network --since last_action` includes the most recent action's own requests; numeric `--since N` remains exclusive. Route patterns use `*` as wildcard and a literal `?` query separator: `*/items?*` and `*/items/?*` differ by the slash.

Start with `daemon start --log-level info` to log request entered/acquired/started/finished times and session IDs; it overrides `daemon.log_level` for that process. On Unix, `kill -USR1 <daemon-pid>` writes asyncio task stacks without ptrace. `cloak version --json`, `/health` and `daemon.json` include `build_id`; `--version` also prints it. A content fingerprint distinguishes installed wheels and local modifications; Git checkouts additionally show their commit.
