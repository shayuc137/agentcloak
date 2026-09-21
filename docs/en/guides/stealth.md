# Stealth and anti-detection

agentcloak is built for sites that actively try to detect and block automation. The default backend is **CloakBrowser**, a patched Chromium with 57 C++ modifications targeting common fingerprinting vectors, plus optional behavioural humanisation. For HTTP traffic, **httpcloak** matches your browser's TLS fingerprint so even raw `fetch` calls look like real Chrome.

This guide explains what's protected, how to turn the dials, and how to verify everything works.

## Quick start

The defaults already use the strongest local stealth tier:

```bash
cloak navigate "https://example.com"     # CloakBrowser, headless by default in v0.2.0
```

For maximum stealth on tough targets, switch off headless mode, turn humanise on, and route through a residential proxy:

```toml
# ~/.agentcloak/config.toml
[browser]
headless = false   # headed mode survives more bot checks than headless
humanize = true    # human-like mouse curves and typing cadence
proxy = "socks5://user:pass@residential.example:1080"   # residential IP defeats IP reputation checks
dns_over_https = false   # default; respects your system DNS / split-horizon proxy
```

Then verify against bot detection benchmarks (see [Verification](#verification) below).

## Three-tier architecture

| Tier | Backend | Stealth | Use case |
|------|---------|---------|---------|
| 1 (default) | **CloakBrowser** | 57 C++ patches, headed-by-default capable, optional humanise | Most sites with anti-bot |
| 2 (fallback) | **PlaywrightContext** | None | Sites with no detection, debugging |
| 3 (real) | **RemoteBridge** | Inherent (real Chrome) | Sites that profile long-term user history |

Switch tiers via config:

```toml
[browser]
default_tier = "cloak"       # or "playwright", "remote_bridge"
```

Or env var: `AGENTCLOAK_DEFAULT_TIER=cloak`.

## CloakBrowser: what's patched

CloakBrowser ships a patched Chromium binary with 57 C++ patches plus driver-level fixes. Coverage:

- **`navigator.webdriver`** removed at the C++ level (not deletable from JS — patched in `WebViewImpl`)
- **Automation flags** stripped (`--enable-automation` / `--enable-blink-features=AutomationControlled` never set)
- **Canvas / WebGL fingerprint** noise injection per browsing session
- **Audio fingerprint** small-noise modulation
- **Font enumeration** matches a believable Windows install list
- **WebGPU + GPU info** spoofing to common consumer GPUs
- **Platform spoofing**: a Linux server reports a Windows fingerprint to mimic the typical desktop user
- **Proxy authentication** including SOCKS5 with credentials (Playwright/Patchright break here)
- **CDP traces** scrubbed (no `cdc_` window properties, no remote-debugging marker)

The binary auto-downloads on first use to `~/.cloakbrowser/` (~200 MB). A background process checks for updates hourly.

## Humanise mode

When `humanize = true`, every action grows a human-shaped behavioural layer:

- Mouse moves trace Bezier curves between current and target with realistic acceleration
- Typing inserts micro-delays between keystrokes with occasional bursts/pauses
- Scrolling uses smooth acceleration/deceleration rather than instant jumps
- Click points jitter inside the target rect rather than dead-centre

Enable per-config:

```toml
[browser]
humanize = true
```

Or per env: `AGENTCLOAK_HUMANIZE=true`.

The cost is latency — actions take 200-1000 ms longer. Worth it on bot-walled sites, overkill on internal dashboards.

## Cloudflare Turnstile bypass

CloakBrowser ships a Manifest V3 extension that patches `window.screenX` / `screenY` to defeat Cloudflare Turnstile's monitor-position check (the most common reason Turnstile fails on headless setups). The extension loads automatically; no manual setup.

The extension helps pass the JS challenge phase (passive verification). Interactive Turnstile challenges (the "Verify you are human" checkbox) require profile reuse with pre-established trust, or human intervention. You can detect the challenge with `cloak snapshot --frames` and interact via `cloak frame focus` + `cloak click`.

For visual CAPTCHAs (hCaptcha, sliders), you still need a human-in-the-loop or a paid solver service.

## httpcloak: TLS fingerprint matching for `fetch`

When the daemon is using CloakBrowser, it launches a local HTTP proxy via [`httpcloak`](https://pypi.org/project/httpcloak/) that re-issues `cloak fetch` requests with a **TLS fingerprint matched to the bundled Chromium version** (JA3/JA4 + HTTP/2 frame ordering). This prevents the classic giveaway where Python's `urllib`/`httpx` send a request that *looks* like Chrome at HTTP layer but advertises a Python TLS handshake.

```bash
cloak fetch "https://tls.peet.ws/api/all"
# the JA3/JA4 hash returned should match a real Chrome of the same major version
```

httpcloak's preset is auto-synced to CloakBrowser's `CHROMIUM_VERSION`:

```python
preset = f"chrome-{chrome_major}"   # e.g. "chrome-139"
LocalProxy(port=0, preset=preset, tls_only=True)
```

If the exact preset is missing, the daemon falls back to `chrome-latest` and logs a warning. The proxy is `tls_only=True`, meaning it only intercepts HTTPS — plain HTTP goes direct.

If httpcloak isn't installed, `cloak fetch` still works but uses plain httpx (TLS fingerprint exposed). Reinstall with `pip install agentcloak --upgrade`.

## Verification

The standard bot-test pages:

```bash
cloak navigate "https://bot.sannysoft.com" --snapshot
cloak navigate "https://abrahamjuliot.github.io/creepjs/" --snapshot
cloak navigate "https://browserleaks.com/canvas" --snapshot
cloak navigate "https://tls.peet.ws/api/all" --snapshot   # for httpcloak / fetch
```

On `bot.sannysoft.com` you want a sea of green ticks; a few yellow warnings on WebDriver flags are acceptable when running headless (turn `headless = false` to clear them).

On `creepjs` you want a fingerprint stability score near a real browser's (50-70). 100 is suspicious — too-stable fingerprints look automated.

For `cloak fetch`: visit `https://tls.peet.ws/api/all` via `fetch`, then via a real Chrome of the same major version. The `ja4` hash should match.

## When stealth isn't enough

Some categories of detection require the real-Chrome route via RemoteBridge:

- **Trust-built-over-time** signals (Google account age, cookie history)
- **Hardware-level fingerprints** that a patched headless can't fake (GPU model, audio device list)
- **Extension presence** that the site detects (uBlock, password manager)

For those, switch the daemon's tier to `remote_bridge` and drive your actual desktop Chrome. See the [Remote Bridge guide](./remote-bridge.md).

## Proxy and network config

Browser fingerprint stealth is necessary but not sufficient — if the request originates from a data-center IP, services like Cloudflare and Google will flag you on **IP reputation** alone. A residential proxy bridges that gap.

```bash
cloak config set browser.proxy "socks5://user:pass@residential.example:1080"
```

CloakBrowser supports SOCKS5 (with credentials), HTTP, and HTTPS proxy schemes. The `proxy` setting only affects the browser; `cloak fetch` still goes through httpcloak's local TLS proxy.

### DNS-over-HTTPS (DoH)

Chromium enables secure DNS (DoH) by default, which sends DNS queries directly to Google's DoH resolver — bypassing your system DNS, transparent proxies, and split-horizon setups. agentcloak **disables DoH by default** (`dns_over_https = false`) so the browser respects your system resolver.

If you explicitly need DoH (e.g., in a network without DNS):

```bash
cloak config set browser.dns_over_https true
```

### Extra Chromium arguments

For edge cases not covered by named config keys:

```bash
cloak config add browser.extra_args "--disable-background-networking"
cloak config add browser.extra_args "--lang=ja-JP"
```

All three settings require a daemon restart to take effect.

## Common pitfalls

- **Data-center IP** — the #1 reason for blocks after fingerprint checks pass. Use `browser.proxy` with a residential proxy
- **DNS bypassed by DoH** — if your network uses DNS-based routing (split-horizon, transparent proxy), make sure `dns_over_https = false` (the default)
- **Headless on a tough site** — try `headless = false` first; you'll see more "this won't load" sites clear immediately
- **No Xvfb on a headless server** — CloakBrowser auto-starts Xvfb but it must be installed (`sudo apt-get install xvfb` etc., see `cloak doctor`)
- **Mismatched httpcloak preset** — if you pinned `cloakbrowser` to a major version the proxy doesn't have, you get `chrome-latest` fallback; usually fine but verify with `tls.peet.ws`
- **Patchright leftover** — pre-v0.2.0 configs used `default_tier = "patchright"`. Change to `"playwright"` or `"cloak"`

Runtime visual emulation (`viewport set --dpr` and `emulate`) deliberately changes values visible to the page. Media settings support headless mode; pointer emulation requires a headed browser. These commands do not provide a complete mobile fingerprint or alter launch settings. See [CLI emulation](../reference/cli.md#emulate).
