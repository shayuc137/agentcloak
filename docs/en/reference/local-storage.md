# Local Storage

agentcloak stores data in two directories on your machine. This page documents what each file does, how long it persists, and disk usage.

## ~/.agentcloak/

The main data directory. Created on first run. Typical size: **< 1 MB** (excluding profiles).

| File/Directory | Purpose | Size | Lifecycle |
|---------------|---------|------|-----------|
| `config.toml` | User configuration | < 1 KB | Permanent, user-managed |
| `daemon.pid` | Running daemon process ID | < 1 KB | Created on daemon start, stale file removed on next start |
| `active-session.json` | Current daemon session info (port, stealth tier, bridge token) | ~130 bytes | Overwritten on daemon start |
| `resume.json` | Last action summary for session resume | < 1 KB | Overwritten on each action, persists after daemon stop |
| `cookies-snapshot.json` | Cookie recovery fallback when no profile is active | Usually < 100 KB | Overwritten by `cloak cookies export` without `--output` |
| `logs/` | Reserved for future log file output | Empty | Daemon logs go to stderr, not to files |
| `profiles/` | Saved browser sessions plus `hide.json` and cookie recovery snapshots | 1-50 MB each | Permanent until `cloak profile delete` |

### Profiles

Profiles are the only component that grows with usage. Each profile stores a full Chromium user data directory. Size depends on site complexity:

- Simple login session: ~1-5 MB
- Complex SPA with cached assets: ~20-50 MB

```bash
cloak profile list          # see all saved profiles with sizes
cloak profile delete NAME   # remove a specific profile
```

There is no automatic expiry or size limit for profiles.

### Logs

Daemon logs are sent to stderr (visible in terminal or captured by process managers). The `logs/` directory exists but is currently unused. A future release may add file-based logging with rotation.

## ~/.cloakbrowser/

CloakBrowser's patched Chromium binary. Managed by the `cloakbrowser` package, not agentcloak directly.

| Content | Size | Lifecycle |
|---------|------|-----------|
| Patched Chromium binary + dependencies | **~200 MB - 1.4 GB** | Downloaded on first use, persists across upgrades |

This is by far the largest disk consumer. To reclaim this space:

```bash
rm -rf ~/.cloakbrowser/
# Re-downloads automatically on next agentcloak use
```

## In-memory only (not on disk)

These are held in daemon memory and lost when the daemon stops:

| Data | Purpose | Limit |
|------|---------|-------|
| Ring buffer | Network events, console logs | Fixed capacity: 1000 events |
| Snapshot cache | Last accessibility tree per tab | 1 per tab |
| Capture store | Recorded network traffic (`cloak capture start`) | Unbounded until `cloak capture stop/clear` |

## Disk usage summary

| Location | Typical size | Can grow? |
|----------|-------------|-----------|
| `~/.agentcloak/` (without profiles) | < 1 MB | No |
| `~/.agentcloak/profiles/` | 0 - 500 MB | Yes, with each saved profile |
| `~/.cloakbrowser/` | 200 MB - 1.4 GB | No (fixed binary) |
| **Total** | **~200 MB - 2 GB** | Depends on profile count |

## Configuration that affects storage

| Setting | Effect |
|---------|--------|
| `idle_timeout_min = 30` | Auto-stop daemon after idle, freeing in-memory resources |
| `stop_on_exit = true` | Stop daemon when CLI exits |

There are currently no configuration options for disk space limits, automatic cleanup, or log rotation. Profiles must be managed manually.

## Complete cleanup

```bash
rm -rf ~/.agentcloak/     # all config, profiles, runtime files
rm -rf ~/.cloakbrowser/   # browser binary cache
```
