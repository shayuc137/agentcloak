# 浏览器后端

agentcloak 支持三种浏览器后端。每个后端都继承 `BrowserContextBase` ABC 抽象基类，所有 CLI 命令和 MCP 工具在不同后端下表现一致。

## 概览

| 后端 | 隐身等级 | 浏览器 | 适用场景 |
|------|---------|--------|---------|
| **CloakBrowser**（默认） | 高 | 补丁版 Chromium（57 个 C++ 补丁） | 大多数网站，反爬绕过 |
| **Playwright** | 无 | 标准 Chromium | 简单自动化，调试 |
| **RemoteBridge** | 真实指纹 | 用户的 Chrome | 登录会话，浏览器扩展 |

## CloakBrowser（默认）

CloakBrowser 搭载了 57 个 C++ 修改的补丁版 Chromium 二进制文件，可以对抗常见的指纹识别和机器人检测。作为默认后端，无需额外参数。

```bash
cloak navigate "https://example.com"
```

### CloakBrowser 补丁内容

- 浏览器指纹随机化（canvas、WebGL、audio、字体）
- C++ 层面移除 `navigator.webdriver` 标志
- 自动化指示器屏蔽（移除 `--enable-automation`）
- 平台伪装（Linux 服务器报告 Windows 指纹）
- 代理认证支持（包括 SOCKS5）

### 拟人模式

CloakBrowser 可以模拟人类行为：贝塞尔曲线鼠标轨迹、带偶尔打字错误的真实打字节奏、带加减速的平滑滚动。

通过配置或环境变量启用：

```toml
# ~/.agentcloak/config.toml
[browser]
humanize = true
```

```bash
# 或通过环境变量
export AGENTCLOAK_HUMANIZE=true
```

### 有头与无头模式

CloakBrowser 默认以有头模式运行，因为反爬系统会检测无头浏览器。在没有显示器的服务器上，agentcloak 自动启动 Xvfb（虚拟帧缓冲区）。

```bash
# 在 Debian/Ubuntu 上安装 Xvfb
sudo apt-get install -y xvfb
```

在桌面环境（有显示器的 Linux、macOS、Windows）下，有头模式使用真实显示器。

### 二进制管理

CloakBrowser 在首次使用时自动下载 Chromium 二进制文件：

- **大小**：约 200 MB
- **缓存位置**：`~/.cloakbrowser/`
- **更新**：每小时后台检查，自动下载新版本

如需自定义 Chromium 构建，使用 `CLOAKBROWSER_BINARY_PATH` 覆盖二进制文件路径。

## Playwright（后备方案）

标准 Playwright Chromium，不含隐身补丁。适用于没有反爬检测的站点，或调试自动化逻辑。

```bash
export AGENTCLOAK_DEFAULT_TIER=playwright
cloak navigate "https://example.com"
```

> [!WARNING]
> Playwright Chromium 没有隐身能力。有反爬检测的站点很可能会阻止它。生产环境请使用 CloakBrowser。

Playwright 需要单独下载浏览器二进制文件：

```bash
python -m playwright install chromium
```

## RemoteBridge（真实 Chrome）

RemoteBridge 通过 Chrome 扩展和 WebSocket 连接到另一台机器上的真实 Chrome 浏览器。浏览器保留其真实指纹、登录会话和已安装的扩展。

```bash
cloak launch --tier remote_bridge
cloak navigate "https://example.com"
```

### 使用场景

- 需要访问真实登录会话（无需导出 cookie）
- 站点检测长期使用的真实浏览器 profile
- 需要在自动化过程中使用 Chrome 扩展
- 需要真实用户浏览器的实际指纹

### 配置步骤

1. **安装扩展。** 在 Chrome 中加载 `src/agentcloak/bridge/agentcloak-chrome-extension/` 下的未打包扩展（`chrome://extensions` > 开发者模式 > 加载已解压的扩展）。

2. **配置连接。** 点击扩展图标，设置 daemon 的主机/端口。扩展会自动连接。

3. **开始使用。**

```bash
cloak launch --tier remote_bridge
cloak navigate "https://example.com"
```

详细的配置说明、多机器部署和故障排除参见 [Remote Bridge 指南](./remote-bridge.md)。

### RemoteBridge 标签页管理

RemoteBridge 支持标签页接管和会话生命周期管理：

```bash
# 接管已有的标签页
cloak bridge claim --url-pattern "dashboard"

# 结束会话：关闭 agent 标签页
cloak bridge finalize --mode close

# 结束会话：保留标签页给用户
cloak bridge finalize --mode handoff
```

## 切换后端

### 通过配置文件

```toml
# ~/.agentcloak/config.toml
[browser]
default_tier = "cloak"   # 或 "playwright"、"remote_bridge"
```

### 通过环境变量

```bash
export AGENTCLOAK_DEFAULT_TIER=cloak
```

### 通过 CLI 参数

用 `cloak launch` 热切换当前后端：

```bash
cloak launch --tier remote_bridge
cloak navigate "https://example.com"
```

### 后端解析规则

`auto`（默认）解析为 `cloak`。

| 设置值 | 解析为 |
|-------|-------|
| `auto` | `cloak` |
| `cloak` | `cloak` |
| `playwright` | `playwright` |
| `remote_bridge` | `remote_bridge` |

> v0.2.0 移除了旧版 `patchright` 后端值——如有旧的 `config.toml`，请改为
> `default_tier = "playwright"`（或 `cloak`）。

## 对比

| 特性 | CloakBrowser | Playwright | RemoteBridge |
|------|-------------|------------|-------------|
| 隐身补丁 | 57 个 C++ 补丁 | 无 | 不适用（真实浏览器） |
| 反爬绕过 | 高 | 低 | 天然通过 |
| Cloudflare 绕过 | 内置（screenX 补丁） | 不支持 | 天然通过 |
| 浏览器二进制 | 自动下载 | 手动安装 | 用户的 Chrome |
| 有头模式 | 默认（Xvfb 自动） | 可选 | 始终 |
| 拟人支持 | 支持 | 不支持 | 不适用 |
| Profile 持久化 | 支持 | 支持 | 天然具备 |
| 代理支持 | 完整（含 SOCKS5 认证） | 有限 | 不适用 |
| 配置复杂度 | 零 | 一行命令 | 安装扩展 |
| 网页逆向 | 完整（调试器 / 路由 / 流式 / source map） | 完整 | 完整 |

## 网页逆向支持

三种后端都支持 Phase 7b 的网页逆向能力——调试器、网络路由拦截、WebSocket/SSE 流式监控、source map、init script 注入、GraphQL。命令在所有后端上完全一致，详见 [CLI 参考](../reference/cli.md#网页逆向)。

这些能力依赖一条持久 CDP 通道。CloakBrowser 和 Playwright 现在为事件流（调试器暂停、WebSocket 帧）维护一条 **per-tab 持久 `CDPSession`**，与 `cloak cdp endpoint` 等一次性调用使用的短期 session 并存。RemoteBridge 把同样的 CDP 命令通过它的 WebSocket 隧道转发。每个 CDP 域都在首次使用时才惰性 enable，因此从不做逆向的会话能让隐身后端的热路径保持干净。
