# 隐身与反检测

agentcloak 是为主动检测和阻止自动化的站点而设计的。默认后端是 **CloakBrowser**，一个搭载 57 个 C++ 修改的补丁版 Chromium，针对常见指纹向量进行了修改，可选叠加行为拟人化。对 HTTP 流量，**httpcloak** 会把你浏览器的 TLS 指纹匹配上，让原始 `fetch` 调用也像真实 Chrome 发出的。

本指南讲清楚保护了什么、怎么调旋钮、以及如何验证一切都生效了。

## 快速开始

默认设置已经用了最强的本地隐身层级：

```bash
cloak navigate "https://example.com"     # CloakBrowser，v0.2.0 默认 headless
```

最强反检测的目标站点上，关掉 headless、打开 humanize，并走住宅代理：

```toml
# ~/.agentcloak/config.toml
[browser]
headless = false   # 带头模式比 headless 更能通过 bot 检测
humanize = true    # 拟人化鼠标曲线与打字节奏
proxy = "socks5://user:pass@residential.example:1080"   # 住宅 IP 绕过 IP 信誉检测
dns_over_https = false   # 默认值；尊重系统 DNS / split-horizon 代理
```

然后用 bot 检测基准站点验证（见下方 [验证](#验证)）。

## 三层架构

| 层 | 后端 | 隐身 | 适用场景 |
|----|------|------|---------|
| 1（默认） | **CloakBrowser** | 57 C++ 补丁，默认无头，可选有头模式与 humanize | 大多数带反爬的站点 |
| 2（fallback） | **PlaywrightContext** | 无 | 无检测的站点、调试 |
| 3（real） | **RemoteBridge** | 天然（真实 Chrome） | 用长期用户历史画像的站点 |

通过配置切层：

```toml
[browser]
default_tier = "cloak"       # 或 "playwright"、"remote_bridge"
```

或 env：`AGENTCLOAK_DEFAULT_TIER=cloak`。

## CloakBrowser 修了什么

CloakBrowser 提供的补丁版 Chromium 二进制包含 57 个 C++ 补丁加驱动层修复。覆盖：

- **`navigator.webdriver`** 在 C++ 层移除（不是 JS 可删的——在 `WebViewImpl` 里打了补丁）
- **自动化 flag** 剥离（`--enable-automation` / `--enable-blink-features=AutomationControlled` 从不设置）
- **Canvas / WebGL 指纹**按会话注入噪声
- **音频指纹**小噪声调制
- **字体枚举**匹配一份可信的 Windows 安装字体表
- **WebGPU + GPU 信息**伪装为常见消费级 GPU
- **平台伪装**：Linux 服务器报告 Windows 指纹模拟典型桌面用户
- **代理认证**包括带凭证的 SOCKS5（Playwright/Patchright 在这里坏掉）
- **CDP 痕迹**清理（无 `cdc_` window 属性，无远程调试标记）

二进制在首次使用时自动下载到 `~/.cloakbrowser/`（约 200 MB）。后台进程每小时检查更新。

## Humanize 模式

`humanize = true` 时，每个 action 都长出一层人形行为：

- 鼠标移动从当前位置到目标走贝塞尔曲线，带真实的加减速
- 键入在按键之间插入微延迟，偶尔有突发/停顿
- 滚动用平滑加减速而非瞬间跳跃
- 点击点在目标矩形内抖动，而非死中心

按配置启用：

```toml
[browser]
humanize = true
```

或按 env：`AGENTCLOAK_HUMANIZE=true`。

代价是延迟——action 多花 200-1000 ms。值得在反爬强的站点上，对内部 dashboard 是杀鸡用牛刀。

## Cloudflare Turnstile 绕过

CloakBrowser 自带一个 Manifest V3 扩展，给 `window.screenX` / `screenY` 打补丁，挫败 Cloudflare Turnstile 的显示器位置检查（headless 配置下 Turnstile 失败的最常见原因）。扩展自动加载；无需手动配置。

扩展帮助通过 JS challenge 阶段（被动验证）。交互式 Turnstile 挑战（"Verify you are human" 复选框）需要复用已建立信任的 profile，或人工介入。可以用 `cloak snapshot --frames` 检测挑战，通过 `cloak frame focus` + `cloak click` 交互。

视觉验证码（hCaptcha、滑块）仍需人在回路或付费 solver 服务。

## httpcloak：为 `fetch` 匹配 TLS 指纹

daemon 用 CloakBrowser 时，会通过 [`httpcloak`](https://pypi.org/project/httpcloak/) 启动一个本地 HTTP 代理，用**与捆绑 Chromium 版本匹配的 TLS 指纹**（JA3/JA4 + HTTP/2 帧顺序）重新发出 `cloak fetch` 请求。这避免了经典破绽：Python 的 `urllib`/`httpx` 发出的请求 HTTP 层看似 Chrome，但 TLS 握手暴露了 Python。

```bash
cloak fetch "https://tls.peet.ws/api/all"
# 返回的 JA3/JA4 哈希应与相同 major 版本的真实 Chrome 一致
```

httpcloak 的 preset 自动同步 CloakBrowser 的 `CHROMIUM_VERSION`：

```python
preset = f"chrome-{chrome_major}"   # 如 "chrome-139"
LocalProxy(port=0, preset=preset, tls_only=True)
```

精确 preset 缺失时，daemon 回退到 `chrome-latest` 并打日志告警。代理是 `tls_only=True`，只拦截 HTTPS——纯 HTTP 直连。

未安装 httpcloak 时，`cloak fetch` 仍能用但走纯 httpx（TLS 指纹暴露）。`pip install agentcloak --upgrade` 重装即可。

## 验证

标准 bot 测试页：

```bash
cloak navigate "https://bot.sannysoft.com" --snapshot
cloak navigate "https://abrahamjuliot.github.io/creepjs/" --snapshot
cloak navigate "https://browserleaks.com/canvas" --snapshot
cloak navigate "https://tls.peet.ws/api/all" --snapshot   # 给 httpcloak / fetch 用
```

`bot.sannysoft.com` 上你想要一片绿勾；headless 跑时 WebDriver flag 上一些黄色告警可接受（设 `headless = false` 即可清掉）。

`creepjs` 上你想要的指纹稳定性分接近真实浏览器（50-70）。100 反而可疑——过于稳定的指纹看起来像自动化。

`cloak fetch` 的验证：通过 `fetch` 访问 `https://tls.peet.ws/api/all`，再用相同 major 版本的真实 Chrome 访问，`ja4` 哈希应一致。

## 隐身不够时

某些类型的检测需要走 RemoteBridge 真实 Chrome 路线：

- **长期积累的信任**信号（Google 账号 age、cookie 历史）
- **硬件级指纹**补丁版 headless 伪造不了的（GPU 型号、音频设备列表）
- **扩展存在**站点能检测的（uBlock、密码管理器）

这些场景下把 daemon 切到 `remote_bridge` 层，驱动你实际桌面的 Chrome。详见 [Remote Bridge 指南](./remote-bridge.md)。

## 代理与网络配置

浏览器指纹伪装是必要的，但不充分——如果请求来源是数据中心 IP，Cloudflare 和 Google 等服务仅凭 **IP 信誉**就会拦截。住宅代理补上这块短板。

```bash
cloak config set browser.proxy "socks5://user:pass@residential.example:1080"
```

CloakBrowser 支持 SOCKS5（含凭证）、HTTP 和 HTTPS 代理。`proxy` 设置只影响浏览器；`cloak fetch` 仍走 httpcloak 的本地 TLS 代理。

### DNS-over-HTTPS (DoH)

Chromium 默认启用安全 DNS（DoH），直接向 Google 的 DoH 解析器发送 DNS 查询——绕过你的系统 DNS、透明代理和 split-horizon 配置。agentcloak **默认禁用 DoH**（`dns_over_https = false`），让浏览器尊重系统 resolver。

如果你明确需要 DoH（比如网络中没有 DNS）：

```bash
cloak config set browser.dns_over_https true
```

### 额外 Chromium 参数

覆盖命名配置项之外的边缘需求：

```bash
cloak config add browser.extra_args "--disable-background-networking"
cloak config add browser.extra_args "--lang=ja-JP"
```

以上三项设置修改后都需要重启 daemon 才能生效。

## 常见陷阱

- **数据中心 IP**——指纹检查过了还被拦截的首要原因。用 `browser.proxy` 配住宅代理
- **DoH 绕过 DNS**——如果你的网络用 DNS 分流（split-horizon、透明代理），确保 `dns_over_https = false`（默认值）
- **难站点上跑 headless**——先试 `headless = false`；许多"加载不动"的站点会立刻通过
- **headless 服务器没装 Xvfb**——CloakBrowser 会自动启动 Xvfb 但必须先安装（`sudo apt-get install xvfb` 等，见 `cloak doctor`）
- **httpcloak preset 不匹配**——如果你把 `cloakbrowser` 钉到代理没有的 major 版本，会拿到 `chrome-latest` 回退；通常没事，但用 `tls.peet.ws` 验证一下
- **遗留的 patchright 配置**——v0.2.0 前的配置用 `default_tier = "patchright"`，改成 `"playwright"` 或 `"cloak"`

运行时视觉模拟（`viewport set --dpr` 和 `emulate`）会主动改变页面可见的值。媒体模拟支持无头模式；指针模拟要求有头浏览器。这些命令不提供完整的移动设备指纹，也不改变启动参数，详见 [CLI 模拟说明](../reference/cli.md#emulate)。
