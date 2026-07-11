# Spells

A **spell** is a reusable, named site operation — one command per site task. Instead of asking the agent to re-figure-out "how do I search GitHub repos by language", you write a `github/repos-by-language` spell once, then `cloak spell run github/repos-by-language lang=python` from anywhere. Spells encode the cheapest viable transport (often a direct API call) so they're orders of magnitude faster than UI automation.

## Quick start

```bash
cloak spell list                              # see what's registered
cloak spell info httpbin/headers              # detail for one spell
cloak spell run httpbin/headers               # execute it
cloak spell run weather/current city=Tokyo    # spells with args use key=value
```

## Strategies

Every spell declares a `Strategy` that tells the executor what it needs:

| Strategy | What it does | CLI execution |
|----------|--------------|---------------|
| `PUBLIC` | Plain HTTP call, no auth | Local, daemon-free |
| `COOKIE` | Replay HTTP with the browser's cookies | Daemon + caller session |
| `HEADER` | Replay HTTP with captured auth headers | Daemon + caller session |
| `INTERCEPT` | Hook a real browser request to extract its result | Daemon + caller session |
| `UI` | Drive the page through clicks / fills | Daemon + caller session |

Always pick the lowest-strategy you can. `PUBLIC` is free of browser overhead; `UI` is the fallback for sites with no usable API. The pattern analyser (`cloak capture analyze`) tells you which strategy the site supports.

## Commands

| Command | Purpose |
|---------|---------|
| `cloak spell list` | List every registered spell with site, name, strategy |
| `cloak spell info SITE/NAME` | Strategy, args, domain, description, source location |
| `cloak spell run SITE/NAME` | Execute locally for PUBLIC; use daemon/browser for other strategies |
| `cloak spell run SITE/NAME k=v k2=v2` | Pass `Arg` values positionally as `key=value` pairs |
| `cloak spell scaffold SITE` | Generate spell stubs from `cloak capture analyze` output |

Spell names are always `site/command` — the site groups related spells, the command identifies the operation.

`cloak spell run` discovers metadata locally before choosing a path. PUBLIC spells
run in the CLI process without contacting or starting the daemon. COOKIE, HEADER,
INTERCEPT, and UI spells call the existing daemon endpoint, which auto-starts as
usual and injects the caller's current Agentcloak session. The CLI never constructs
a separate browser. This routing does not persist credentials or refresh expired
sessions.

## Writing a spell

Two modes: pipeline (declarative) and function (Python code).

### Pipeline mode

For straight-line API calls, declare a list of steps. Built-in steps include `fetch`, `select`, `extract`, `transform`, and template interpolation with `{args.name}`:

```python
from agentcloak.core.types import Strategy
from agentcloak.spells.registry import spell

@spell(
    site="httpbin",
    name="headers",
    strategy=Strategy.PUBLIC,
    description="Inspect request headers via httpbin.org",
    pipeline=[
        {"fetch": {"url": "https://httpbin.org/headers"}},
        {"select": "headers"},
    ],
)
def httpbin_headers() -> None:
    """Pipeline placeholder — the body is unused for pipeline spells."""
```

The decorated function is just a placeholder when `pipeline=` is set; the registry stores the pipeline and executes it.

The `fetch` step inherits browser session state when one is available (cookies, live `User-Agent`, proxy from `cloak fetch`). For `PUBLIC` spells without a browser, it falls back to a CloakBrowser-version-aligned Chrome `User-Agent` so the UA matches what a stealth session would send. Override by passing `headers={"User-Agent": "..."}`; add a spell `arg` if the value needs to be configurable per invocation.

### Function mode

For anything that needs branching, multi-step browser interaction, or computed args, write an async handler:

```python
from agentcloak.core.types import Strategy
from agentcloak.spells.context import SpellContext
from agentcloak.spells.registry import spell

@spell(
    site="example",
    name="title",
    strategy=Strategy.COOKIE,
    domain="example.com",
    description="Get the page title of example.com",
)
async def example_title(ctx: SpellContext) -> list[dict[str, object]]:
    title = await ctx.evaluate("document.title")
    url = await ctx.evaluate("location.href")
    return [{"title": title, "url": url}]
```

`SpellContext` exposes browser operations: `ctx.navigate(url)`, `ctx.evaluate(js)`, `ctx.click(target)`, `ctx.fetch(url, ...)`. Return a `list[dict]` — each entry becomes one row of the spell's output.

When `strategy` is `COOKIE` / `HEADER` and `domain` is set, the executor automatically navigates to `https://<domain>` before invoking the handler so the cookies/headers are populated.

## Discovery

The CLI and daemon discover spells from two locations:

1. **Built-in:** `src/agentcloak/spells/sites/` — ships with the package, includes the `httpbin` and `example` examples
2. **User directory:** `~/.config/agentcloak/spells/*.py` — yours; survives package updates

Any `.py` file in either location is imported; its `@spell(...)` decorators register at import time. CLI list/info/run scans locally. Browser-backed runs are rediscovered by the daemon before execution, so place user spells where both processes resolve the same user config directory.

## Capture → analyze → scaffold

The capture system feeds spell generation. The full pipeline:

```bash
cloak capture start
# do the workflow manually in the browser
cloak capture stop
cloak capture analyze --domain target-site.com
cloak spell scaffold target-site --domain target-site.com
```

`spell scaffold` writes one Python file per endpoint cluster under `~/.config/agentcloak/spells/`, with the strategy already inferred from the captured auth, the URL templated against detected path parameters, and an `Arg` per variable segment. You refine the generated stubs, test, and ship.

See the [capture guide](./capture.md) for the recording side.

## Conventions

- **Naming:** `site/verb-noun` — `github/repos-by-language`, `hn/top-stories`, `linear/issue-status`
- **One file per site** under `~/.config/agentcloak/spells/`, multiple `@spell` decorators inside
- **Args:** kebab-case names so they survive `key=value` parsing on the command line
- **Output:** always `list[dict]` so the JSON envelope is consistent across spells
- **Side effects:** mark `access="write"` in the decorator so callers can audit destructive spells
