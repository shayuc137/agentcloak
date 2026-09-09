# Contributing to agentcloak

Thanks for your interest in contributing. This guide covers setup, code style, testing, and PR workflow.

## Quick Setup

```bash
git clone https://github.com/shayuc137/agentcloak.git
cd agentcloak
uv sync --locked --extra dev
```

The CloakBrowser binary (~200 MB) downloads automatically on first use. No manual browser install needed.

**Headless Linux servers** also need Xvfb for CloakBrowser's headed mode:

```bash
sudo apt-get install -y xvfb
```

## Development Workflow

1. Create a branch from `main`:

   ```bash
   git checkout -b feat/my-feature
   ```

2. Make your changes

3. Run the complete quality gate before committing:

   ```bash
   uv run --locked python scripts/preflight.py
   ```

   This runs unit tests, lint, format, strict type checking, generated-data and documentation checks, and CLI smoke checks.

4. Run relevant integration tests when browser behavior changes:

   ```bash
   uv run --locked pytest tests/integration/ -x  # needs browser binaries
   ```

5. Push and open a PR

## Code Style

**Linting:** [ruff](https://docs.astral.sh/ruff/) with the project config in `pyproject.toml`. The rule set includes pycodestyle, pyflakes, isort, pyupgrade, bugbear, and simplify.

```bash
ruff check src/              # lint
ruff format src/             # format
```

**Type checking:** [pyright](https://github.com/microsoft/pyright) in strict mode.

```bash
pyright src/
```

**General rules:**

- Target Python 3.12+
- Line length: 88 characters
- Use `from __future__ import annotations` is not needed (3.12+ native)
- Prefer `X | Y` union syntax over `Union[X, Y]`

## Project Structure

```
src/agentcloak/
  cli/          # typer CLI commands (talks to daemon over HTTP)
  daemon/       # aiohttp daemon (manages browser lifecycle)
  browser/      # browser backends (BrowserContext protocol)
  core/         # shared utilities, config, types
  mcp/          # MCP server (FastMCP, talks to daemon over HTTP)
  spells/       # spell registry and built-in spells
  bridge/       # RemoteBridge (Chrome extension + WS hub)
```

## Layer Isolation

Layer boundaries are strictly enforced:

| Layer | Can import | Cannot import |
|-------|-----------|---------------|
| `cli/` | daemon HTTP API | `browser/`, `daemon/` internals |
| `daemon/` | `browser/`, `core/` | `cli/` |
| `browser/` | `core/` | `cli/`, `daemon/` |
| `core/` | stdlib + third-party | any sibling layer |
| `spells/` | `core/`, `browser/protocol` | `daemon/`, `cli/` |
| `mcp/` | `core/`, `spells/`, daemon HTTP API | `browser/`, `daemon/` internals |

## Adding a New Feature

When adding a new capability, it must be exposed through the full stack:

1. **Daemon route** in `src/agentcloak/daemon/routes.py`
2. **CLI command** in `src/agentcloak/cli/commands/`
3. **MCP tool** in `src/agentcloak/mcp/tools/`
4. **Skill file** update in `skills/agentcloak/SKILL.md` and `.claude/skills/agentcloak/SKILL.md`

Run `python3 scripts/check_consistency.py` to verify alignment across all three layers.

## Testing

**Unit tests** (`tests/unit/`): fast, no browser or daemon needed. These run in CI on every push.

**Integration tests** (`tests/integration/`): require a running daemon and browser. CI runs navigation, screenshot, and JavaScript smoke tests on both Playwright and CloakBrowser. Run the broader suite locally when changing browser behavior.

```bash
pytest tests/unit/ -x           # quick feedback loop
pytest tests/ -x                # everything
```

Mark tests that need network access:

```python
@pytest.mark.network
def test_real_site():
    ...
```

## Pull Request Guidelines

- One logical change per PR
- Include tests for new functionality
- Update documentation if behavior changes (README, docs/, Skill files)
- Link to a relevant issue if one exists
- CI must pass: preflight quality gate, unit-test matrix, build verification, and dependency audits

## CI

GitHub Actions runs on pushes and pull requests targeting `main`:

- **Preflight** quality checks, using the committed `uv.lock`
- **Unit tests** on Linux and Windows (Python 3.12–3.14), and macOS (Python 3.12–3.13), using the same lockfile
- **Browser smoke tests** on Linux for Playwright and CloakBrowser: navigation, JPEG screenshots, and JavaScript execution against local pages
- **Build verification** for wheel and sdist, including a fresh wheel installation and CLI smoke check
- **Dependency audits** for the locked dependencies (all extras) and a fresh installation

### Updating dependencies

Review upstream release notes, then refresh and validate the lockfile:

```bash
uv lock --upgrade
uv sync --locked --extra dev
uv run --locked python scripts/preflight.py
uv export --locked --all-extras --no-emit-project --no-hashes --output-file /tmp/agentcloak-audit.txt
uvx pip-audit --strict --no-deps --disable-pip --requirement /tmp/agentcloak-audit.txt
```

Commit `pyproject.toml` and `uv.lock` together if dependency requirements change. The export audit uses exact locked versions without re-resolving them; platform markers are evaluated on the audit runner (Linux in CI). Keep the separate fresh-install audit to cover dependency resolution for pip users. Browser upgrades also need relevant browser/bridge smoke checks.

MCP remains on the supported 1.x line (`<2.0.0`) until the server API migration is implemented. The explicit cryptography minimum prevents existing environments from keeping the vulnerable version addressed by CVE-2026-69247.

## Questions?

Open a [discussion](https://github.com/shayuc137/agentcloak/discussions) or file an [issue](https://github.com/shayuc137/agentcloak/issues).
