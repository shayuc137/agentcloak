# Data Extraction & Spells

## JavaScript Evaluation

```bash
cloak js evaluate "document.title"
cloak js evaluate "document.querySelectorAll('a').length"
cloak js evaluate "JSON.stringify(window.__NEXT_DATA__)" # extract Next.js data
cloak js evaluate --file probe.js                         # multiline script
```

Use exactly one of inline code, `--file`, or `--preset`. Failed evaluations return
the thrown message plus the first useful source location instead of a bare
`Uncaught`; diagnostics are capped at 400 characters.

Use `--world isolated` for an isolated context (no page globals pollution). Only `main` (default) and `isolated` are valid.

## HTTP Fetch with Browser Cookies

Fetch URLs using the browser's authenticated session:

```bash
cloak fetch "https://api.example.com/data"
cloak fetch "https://api.example.com/submit" --method POST --body '{"key": "value"}'
cloak fetch "https://api.example.com/data" -H "Authorization: Bearer xyz" -H "X-Api-Key: k"
cloak fetch "https://api.example.com/report.csv" --output report.csv
```

Cookies and the browser user agent are synced from the session automatically. Add custom headers with `--header/-H "Key: Value"` (repeatable — they layer on top of the synced ones). The response body prints to stdout; `--output/-o path` writes it to a file instead (status line still goes to stderr).

## Network Capture

Record all network traffic, then export or analyze:

```bash
cloak capture start
cloak navigate "https://api-heavy-site.com"
# interact with the site...
cloak capture stop
cloak capture export --format har -o traffic.har   # --format har|json, --output/-o writes to file (HAR can be large)
cloak capture analyze --domain api.example.com  # pattern detection; --domain scopes to one host (omit for all)
cloak capture replay "https://api.example.com/data" --method POST  # replay a captured request (URL positional, --method/-m default GET)
cloak capture clear      # clear recorded data
```

The analyzer detects: path parameters, endpoint clusters, authentication methods, and request schemas. `analyze --domain` narrows the report to a single host when capture spans several. `export` without `--output` streams to stdout; with `-o` it writes the raw HAR/JSON to disk.

## Spells (Reusable Site Automation)

Spells are pre-built commands for specific websites. Think of them as "refined recipes" — crafted once, cast with one line.

```bash
cloak spell list                        # see available spells
cloak spell info httpbin/headers        # show spell details (lists any args)
cloak spell run httpbin/headers         # execute a spell
cloak spell run mysite/search query=shoes page=2  # args are positional key=value (no --arg flag)
cloak spell scaffold mysite             # generate template for a new spell
```

Built-in spells are minimal — only `httpbin/headers` and `example/title` ship by default. `cloak spell info <site/name>` shows whether a spell takes arguments; pass them as positional `key=value` pairs (`cloak spell run` has no `--arg` flag).

Dispatch follows spell metadata. `Strategy.PUBLIC` runs inside the CLI process and
does not contact the daemon. COOKIE, HEADER, INTERCEPT, and UI strategies call
`/spell/run`; daemon auto-start and session headers work like other browser commands,
so the spell receives the caller's current browser context. This is execution
routing only—credential persistence, expiry detection, and refresh are separate.

### Creating Spells

Two modes:

**Pipeline mode** (declarative, for API calls):
```python
@spell(site="httpbin", name="headers", strategy=Strategy.PUBLIC,
       pipeline=[{"fetch": "https://httpbin.org/headers"}, {"select": "headers"}])
```

**Function mode** (code, for browser interaction):
```python
@spell(site="example", name="title", strategy=Strategy.COOKIE)
async def get_title(ctx: SpellContext):
    title = await ctx.evaluate("document.title")
    return [{"title": title}]
```

Spells are discovered from built-in `spells/sites/` and user directory `~/.config/agentcloak/spells/`.

### Capture-to-Spell Pipeline

Observe API traffic → auto-generate spell:

```bash
cloak capture start
# browse the site, let it make API calls...
cloak capture stop
cloak capture analyze           # identifies API patterns
cloak spell scaffold mysite     # generates spell code from analysis
```

## Batch Operations

Execute multiple actions in one call with `--calls-file`:

```bash
echo '[
  {"action": "fill", "target": "3", "text": "hello"},
  {"action": "click", "target": "5"},
  {"action": "wait", "condition": "selector", "value": ".result"}
]' > batch.json
cloak do batch --calls-file batch.json
```

Use `$N.path` to reference prior action results:
```json
[
  {"action": "click", "target": "3"},
  {"action": "fill", "target": "5", "text": "$0.data.url"}
]
```

Batch stops on URL change, focus change, or dialog — returns partial results.
