# Token Optimization

Pick the cheapest command that answers the question, and recover from errors with a known sequence instead of guessing. The browser is stateful — the daemon holds the full snapshot — so most observation can be far cheaper than a screenshot.

## Cheapest-Path Decision Tree

```
What do you need?
├─ Already know the element ref? ──────────> act directly: cloak click N / cloak fill N "..."
│                                            (skip the snapshot entirely)
├─ A single fact from the DOM? ───────────> cloak js evaluate "expr"     (precise, near-zero overhead)
├─ Page text only (article, results)? ────> cloak snapshot --mode content
├─ Interactive elements to act on? ───────> cloak snapshot               (compact, the default — ~1.8K tokens)
│     ├─ stable content selector? ─────────> cloak snapshot --within main (scope tree + refs before rendering)
│     └─ page is huge (100+ nodes)? ──────> cloak snapshot --limit 50, then --focus N / --offset N
├─ Full structure incl. containers? ──────> cloak snapshot --mode accessible   (heavier)
├─ Just what changed since last look? ────> cloak snapshot --diff
├─ Raw HTML (last resort)? ───────────────> cloak snapshot --mode dom      (large)
└─ Visual layout / pixels matter? ────────> cloak screenshot              (most expensive — see below)

Acting and observing in one call? add --snap to the action:
  cloak click 5 --snap     # action result + compact snapshot, one round-trip
```

## Cost Ordering (cheapest → most expensive)

1. `cloak js evaluate "expr"` — returns just the value you asked for
2. `cloak snapshot --mode content` — text, no tree overhead
3. `cloak snapshot --diff` — only the delta since the previous snapshot
4. `cloak snapshot` (compact, default) — interactive + named containers, capped at 80 nodes (~1.8K tokens)
5. `cloak snapshot --mode accessible` — full tree, every container
6. `cloak snapshot --mode dom` — raw HTML, easily 10x the compact tree
7. `cloak screenshot` — image tokens; `--format jpeg` (default) is 4-10x smaller than `--format png`

Rule of thumb: default to compact `cloak snapshot`; only escalate to `screenshot` when visual layout, OCR, or a vision model genuinely needs pixels. Action targets keep working even when a ref was truncated from the printed tree, so a tighter `--limit` rarely costs you reach.

## Recovery Patterns

Each block is **symptom → cause → recovery commands**.

### Stale element ref (`element_not_found` / `[N] not in selector_map`)
The page changed and `[N]` no longer maps. The daemon already auto-retried once with a fresh snapshot.
```bash
cloak snapshot          # get fresh refs
cloak click <new-N>     # retry with the new ref
```

### Blocked by dialog (`blocked_by_dialog`)
A confirm/prompt dialog is intercepting every operation. The stderr line already tells you the dialog text.
```bash
cloak dialog accept                 # OK / confirm
cloak dialog accept --text "reply"  # answer a prompt
cloak dialog dismiss                # Cancel
# then retry the original action
```

### Navigation didn't take (`no_valid_page`)
The last `navigate` failed, so the page is still the previous URL; snapshot/screenshot/click/evaluate refuse to run on stale state. (`fetch` and `network` are unaffected.)
```bash
cloak navigate "<url>" --timeout 60   # re-navigate, give it more time
cloak snapshot
```

### Timeout (`navigation_timeout` / `wait_timeout`)
The page or wait condition didn't resolve in time.
```bash
cloak navigate "<url>" --timeout 60        # one-off longer budget
# or globally:  AGENTCLOAK_NAVIGATION_TIMEOUT=60
cloak wait --selector ".ready" --timeout 15000   # verify the condition is actually reachable
cloak screenshot --wait-selector ".ready" --wait-timeout 15000  # wait + capture in one call
```

### `fill` crawling on a form
Humanize is intercepting `fill` and replaying it character-by-character (~3s per field).
```bash
cloak daemon stop && cloak daemon start --no-humanize -b   # bulk-fill speed
# (or set AGENTCLOAK_HUMANIZE=false before first command)
```

### Daemon down (`daemon_unreachable` / `daemon_auto_start_failed`)
Auto-start should handle the first command; if it didn't, diagnose in-process.
```bash
cloak doctor --fix      # reports + repairs what's missing (works even when the daemon is down)
cloak daemon start -b   # manual start if doctor is clean
```

### Output too large for the budget
A snapshot or evaluate result is blowing past your token budget.
```bash
cloak snapshot --limit 50      # fewer nodes
cloak snapshot --within main   # exclude navigation and sidebars before refs are assigned
cloak snapshot --focus N       # zoom into one subtree
cloak snapshot --mode content  # text only, drop the tree
cloak snapshot --max-chars 4000  # hard cap the printed output
```
