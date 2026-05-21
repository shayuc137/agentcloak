# Quality Guidelines

> Python code quality standards, tooling, and testing conventions.

---

## Overview

This is a Python 3.11+ project using modern tooling. Code quality is enforced through static analysis, type checking, and automated tests. The goal: every file the AI writes should pass CI checks on first commit.

---

## Python Version and Style

- **Minimum Python**: 3.11
- **Type annotations**: required on all public functions and class attributes
- **Formatter**: `ruff format` (Black-compatible, 88 char line width)
- **Linter**: `ruff check` with a curated rule set
- **Type checker**: `pyright` in strict mode
- **Docstrings**: single-line only, explain _why_ (not _what_). No multi-paragraph docstrings

---

## Required Patterns

### Type annotations everywhere

```python
# Do:
async def navigate(self, url: str, *, timeout: float = 30.0) -> ActionResult: ...

# Don't:
async def navigate(self, url, timeout=30.0): ...
```

### Dataclasses for data, Protocol for interfaces

```python
# Do: use dataclass for structured data
@dataclass(frozen=True)
class PageSnapshot:
    seq: int
    url: str
    title: str
    tree_text: str
    selector_map: dict[int, ElementRef]

# Do: use Protocol for pluggable backends
class BrowserContext(Protocol):
    async def navigate(self, url: str) -> ActionResult: ...
```

```python
# Don't: plain dicts for structured data
snapshot = {"seq": 1, "url": "...", "title": "..."}

# Don't: ABC with abstract methods where Protocol suffices
class BrowserContext(ABC):
    @abstractmethod
    async def navigate(self, url: str) -> ActionResult: ...
```

### Keyword-only arguments for optional parameters

```python
# Do:
async def screenshot(self, *, full_page: bool = False, quality: int = 80) -> bytes: ...

# Don't:
async def screenshot(self, full_page=False, quality=80) -> bytes: ...
```

### `__all__` in every `__init__.py`

Explicit public API. Prevents accidental exposure of internal symbols.

---

## Forbidden Patterns

| Pattern | Why | Use instead |
|---------|-----|-------------|
| `Any` type annotation | Defeats type checking | Narrow the type or use `object` |
| `# type: ignore` without code | Silences real errors | `# type: ignore[specific-code]` |
| `import *` | Unclear namespace | Explicit imports |
| Mutable default arguments | Shared state bugs | `field(default_factory=list)` |
| `os.path` for path manipulation | Inconsistent API | `pathlib.Path` |
| `requests` for HTTP | Blocks the event loop, project standard is `httpx` | `httpx.AsyncClient` for daemon paths, `httpx.Client` for CLI scripts |
| Global mutable state | Untestable, race conditions | Pass state via constructor / dependency injection |
| `time.sleep()` in async code | Blocks the event loop | `asyncio.sleep()` |
| Multi-line comments or docstrings | Clutters code, spec handles conventions | Single-line comment if needed |

---

## Testing Requirements

### Test organization

- `tests/unit/` — fast tests, no external dependencies (browser, network, daemon)
- `tests/integration/` — requires daemon and/or browser instance
- Test file naming: `test_<module>.py` mirrors `src/agentcloak/<module>.py`

### What to test

| Component | Required coverage |
|-----------|------------------|
| `core/errors.py` | Envelope construction, serialization, all exception types |
| `core/seq.py` | Counter increment, ring buffer wrap, `since` filtering |
| `cli/commands/*` | Smoke test each command with mocked daemon client |
| `daemon/routes/` | Route registration, request/response shape |
| `browser/protocol.py` | Protocol compliance for each backend (contract tests) |

### Test style

```python
# Do: descriptive test names, arrange-act-assert
def test_error_envelope_serializes_all_three_fields():
    err = NavigationError(error="timeout", hint="slow page", action="retry")
    d = err.to_dict()
    assert d == {"ok": False, "error": "timeout", "hint": "slow page", "action": "retry"}

# Don't: vague names, no assertions
def test_error():
    err = NavigationError(error="x", hint="y", action="z")
    err.to_dict()  # no assertion
```

### Test framework

- `pytest` with `pytest-asyncio` for async tests
- Fixtures in `conftest.py` for shared setup (daemon client, browser context mock)
- No `unittest.TestCase` subclassing

---

## Dependency Management

- `pyproject.toml` as single source of truth (PEP 621)
- Pin direct dependencies with `>=` lower bound, no upper bound
- Dev dependencies in `[project.optional-dependencies.dev]`
- Lock file via `uv lock` for reproducible installs

---

## Pre-Release Preflight

Before any release, run the automated quality gate:

```bash
python scripts/preflight.py
```

9 checks: unit tests, lint, typecheck, CLI/MCP/daemon surface consistency, client drift detection, Skill reference sync, config docs sync, version consistency, CLI smoke test. All must pass (exit 0).

Selective runs: `--only config`, `--skip tests`, `--verbose`.

CI runs `preflight --skip tests` (pytest stays in the matrix job for multi-version coverage).

---

## Code Review Checklist

Before merging, verify:

- [ ] All public functions have type annotations
- [ ] Error cases use `AgentBrowserError` subclasses with all three fields
- [ ] No `print()` calls — use structlog on stderr or one of the `cli.output` primitives (`success`, `value`, `info`, `error`, `json_out`) on stdout
- [ ] New CLI commands go through `dispatch_text_or_json(...)` with a renderer from `core/text_renderers.py`
- [ ] New daemon routes have Pydantic request/response models in `daemon/models/<group>.py`
- [ ] New config fields documented in `docs/en/reference/config.md` + `docs/zh/reference/config.md`
- [ ] Tests exist for new logic
- [ ] New service/manager methods have at least one call site (`rg "method_name" src/` should show callers beyond the definition)
- [ ] `ruff check` and `pyright` pass clean
- [ ] `python scripts/preflight.py` all green

---

## Environment Observations

After completing implementation or review, scan for issues beyond the task scope:

- **Repeated warnings** in command output (deprecation notices, config warnings, noisy logs)
- **Inefficient tool usage** (using `find` where `fd` works, `grep` where `rg` works, etc.)
- **Stale patterns** in touched files (old naming conventions, deprecated API usage, copy-paste code)
- **Missing or outdated tests** for areas adjacent to your changes

Report these at the end of your output — one line per observation. Don't fix them silently, don't ignore them. The human decides whether to act.

---

## Related Specs

- [Error Handling](./error-handling.md) — exception hierarchy and patterns
- [Directory Structure](./directory-structure.md) — where to put new code
