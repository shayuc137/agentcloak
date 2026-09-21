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

On local backends, `close --force` bypasses ordinary scheduling, cancels the selected session's requests, terminates frozen page execution and closes its tabs. It does not restart the daemon or close another session. Session queues and tab ownership are isolated, but sessions in a shared context can share network connection pools and renderer processes. Resource exhaustion or a browser-side stall can therefore affect sibling sessions. Closing the offending session can release shared resources. Workspace storage isolation does not promise a separate browser process per session. It skips renderer-dependent storage capture and retains the workspace context; normal shutdown still persists workspace storage. A disconnected HTTP client cancels its daemon-side request. RemoteBridge force-close returns `force_recovery_unavailable`; use its normal release/reconnect lifecycle.

Unexpected page/browser loss produces `page_recreated` or `page_lost`. Navigate before collecting evidence; a replacement blank page is not silently accepted. `/health` does not build a snapshot or replace element references and bounds its best-effort page information read.

Screenshot JSON includes `url`, `title`, `viewport` (CSS width/height), `dpr`, `pixel_width` and `pixel_height` (decoded from the captured image). Temporary viewport metadata describes the capture, not the restored viewport. The output directory must exist. `--expect-url` uses case-sensitive glob matching; `navigate --expect-path` checks the exact final URL pathname, excluding query and fragment. A mismatch fails without writing a screenshot. Navigation during capture returns `page_changed`.

`cdp endpoint --page` returns the current session's exact page target URL and `target_id` on local backends. Without `--page`, it returns the browser endpoint. Selecting a page is not a CDP security boundary.

Actions and evaluations report `new_tab` and warn when many popups accumulate. A popup still loading may initially report `pending: true` without a tab ID; inspect `tab list` before targeting it. `tab close --others` closes sibling tabs only in this session and returns their IDs in `closed`; repeating it with no siblings returns an empty list. Text output reports the count and IDs. Close finished popups proactively when retaining them is unnecessary. Key names and modifier aliases are case-insensitive; invalid combinations are rejected before keyboard input, and failed/cancelled presses release their newly pressed modifiers. `network --since last_action` includes the most recent action's own requests; numeric `--since N` remains exclusive. Route patterns use `*` as wildcard and a literal `?` query separator: `*/items?*` and `*/items/?*` differ by the slash.

Start with `daemon start --log-level info` to log request entered/acquired/started/finished times and session IDs; it overrides `daemon.log_level` for that process. On Unix, `kill -USR1 <daemon-pid>` writes asyncio task stacks without ptrace. `cloak version --json`, `/health` and `daemon.json` include `build_id`; `--version` also prints it. A content fingerprint distinguishes installed wheels and local modifications; Git checkouts additionally show their commit.

## Long-lived streams and stalled navigation

Chromium normally permits six HTTP/1.x connections per destination in its ordinary socket pool ([upstream implementation](https://github.com/chromium/chromium/blob/main/net/socket/client_socket_pool_manager.cc)). SSE responses stay open: if pages in a shared context fill that pool, navigation and API calls from other sessions to the same destination may wait too. A healthy daemon and an empty session queue do not rule out this browser-network bottleneck. `curl` uses a separate connection pool and may still respond immediately.

Keep only required subscriptions and close finished popups. For an application you control, share subscriptions across tabs (for example, a SharedWorker), unsubscribe when the consumer goes away, or serve the browser-facing stream endpoint over HTTP/2. Check the actual negotiated protocol; an HTTP/2 upstream behind a browser-facing HTTP/1.1 proxy does not remove the browser limit. To distinguish pool exhaustion from other stalls, inspect Chromium NetLog for `SOCKET_POOL_STALLED_MAX_SOCKETS_PER_GROUP` together with the active streaming responses. Raw network logs can contain application data; keep diagnostic artifacts private.

`session close --force` releases the selected session's pages and their connections, allowing blocked sibling sessions to recover. It does not fix duplicate subscriptions in the application. agentcloak does not silently abort application streams or close popups at a fixed tab count.
