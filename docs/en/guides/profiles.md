# Browser profiles

Profiles let you persist login state, cookies, localStorage, extensions, and page-hide selectors across daemon restarts. Each profile is a real Chromium user-data directory under `~/.agentcloak/profiles/<name>/`. Use them for long-lived sessions (logged-in dashboards, multi-account workflows, sites that build trust over time).

## Quick start

```bash
cloak profile create work             # empty profile
cloak profile launch work             # start daemon using that profile
cloak navigate "https://github.com"   # log in once
# next time you launch with --profile work, you're still logged in
```

The launched daemon runs a persistent Chromium context bound to the profile directory — cookies, IndexedDB, and storage all survive restarts.

## Commands

| Command | Purpose |
|---------|---------|
| `cloak profile list` | List every profile and its disk size |
| `cloak profile create NAME` | Create a fresh empty profile |
| `cloak profile create NAME --from-current` | Snapshot the running session into a new profile |
| `cloak profile launch NAME` | Restart the daemon with that profile active |
| `cloak profile delete NAME` | Remove the profile directory (irreversible) |

Profile names must be kebab-case (`work`, `personal-gh`, `client-acme-prod`). The CLI rejects names with spaces, uppercase, or punctuation.

## Saving a session into a profile

You can promote a one-off browse into a reusable profile without re-doing the login flow:

```bash
cloak navigate "https://example.com"
# log in manually via the headed window
cloak profile create example-account --from-current
```

This copies cookies and storage from the live session into `~/.agentcloak/profiles/example-account/`. Next time:

```bash
cloak profile launch example-account
cloak navigate "https://example.com/dashboard"   # already logged in
```

## Launching with a profile

`profile launch` restarts the daemon — running browser sessions are closed first.

```bash
cloak profile launch work                # foreground
cloak profile launch work -b             # background
cloak profile launch work --headed       # force headed mode for this run
cloak profile launch work --port 18800   # custom daemon port
```

You can also wire a default profile via config so every daemon start uses it:

```toml
# ~/.agentcloak/config.toml
[browser]
default_profile = "work"
```

Or via environment variable:

```bash
export AGENTCLOAK_DEFAULT_PROFILE=work
```

Once `default_profile` is set, `cloak daemon start` (or any auto-start) launches that profile by default.

## Multi-account workflows

A common pattern is one profile per identity, switched as the task demands:

```bash
cloak profile list
# { "profiles": [ { "name": "github-personal", ... }, { "name": "github-work", ... } ] }

# task 1: personal account
cloak profile launch github-personal
cloak navigate "https://github.com/notifications"

# task 2: work account
cloak profile launch github-work          # daemon restarts under work profile
cloak navigate "https://github.com/orgs/acme/projects"
```

Because each `launch` restarts the daemon, the profiles don't see each other's state. This is a feature: a `Set-Cookie` written by one account can never leak into the other.

## Directory layout

```
~/.agentcloak/profiles/
├── work/                     # Chromium user-data-dir
│   ├── Default/              # standard Chromium profile structure
│   ├── First Run
│   ├── hide.json             # selectors managed by cloak hide
│   ├── cookies-snapshot.json # latest recovery snapshot from cookies export
│   └── ...
├── github-personal/
└── github-work/
```

The directory is a stock Chromium user-data directory — you can point a regular Chrome instance at it for inspection (`chromium --user-data-dir=~/.agentcloak/profiles/work`).

## Deleting a profile

```bash
cloak profile delete work
```

This removes the entire `~/.agentcloak/profiles/work/` directory. The daemon does not need to be stopped — but if you're currently running that profile, the deletion will fail until you launch a different one.

## Recovering cookies

`cloak cookies export` without `--output` refreshes `cookies-snapshot.json` while still printing the cookies. If a later page loses its login state, restore the snapshot and reload:

```bash
cloak cookies restore
cloak press Control+r
```

Use `--file` to restore another snapshot. Without an active profile, the default moves to `~/.agentcloak/cookies-snapshot.json`.

## Profiles vs cookies export

| Need | Use |
|------|-----|
| Persistent agent identity across runs | Profile |
| One-off transfer of login from real browser | `cloak cookies export` from RemoteBridge → `cloak cookies import` into a profile |
| Short-lived stateless tasks | No profile — use the default ephemeral context |

See the [Remote Bridge guide](./remote-bridge.md) for the cookie-export path.
