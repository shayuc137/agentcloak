# 配置参考

agentcloak 开箱即用，无需任何配置。所有设置都有合理的默认值，可通过配置文件或环境变量覆盖。

## 优先级

设置按以下顺序解析（高优先级优先）：

1. **环境变量**（`AGENTCLOAK_*`）
2. **配置文件**（`~/.agentcloak/config.toml`）
3. **内置默认值**

## 配置文件

位置：`~/.agentcloak/config.toml`

配置文件包含四个段：`[daemon]`、`[browser]`、`[security]` 和 `[bridge]`。

```toml
[daemon]
host = "127.0.0.1"
port = 18765
http_client_timeout = 90
http_connect_timeout = 5.0
auto_start_timeout = 15.0
auto_start_poll_interval = 0.5
log_level = "warning"
log_to_file = false
log_max_bytes = 10000000
log_backup_count = 3

[browser]
default_tier = "auto"
default_profile = ""
viewport_width = 1280
viewport_height = 720
navigation_timeout = 30
idle_timeout_min = 30
stop_on_exit = false
headless = true
humanize = true
action_timeout = 30000
batch_settle_timeout = 2000
max_return_size = 50000
screenshot_format = "jpeg"
screenshot_quality = 80
mcp_screenshot_quality = 50
snapshot_max_nodes = 80
proxy = ""                  # 例：socks5://user:pass@host:1080
dns_over_https = false      # false（默认）会追加 --disable-features=DnsOverHttps
extra_args = []             # 额外 Chromium 启动参数，例 ["--lang=ja-JP"]

[security]
domain_whitelist = []
domain_blacklist = []
content_scan = false
content_scan_patterns = []

[bridge]
# token 在 daemon 首次启动时自动生成；通过 `cloak bridge token --reset` 轮换
local_idle_timeout = 1800
```

> [!NOTE]
> 无效的配置值（端口越界、未知 tier、错误日志级别等）会在启动时报清晰的错误信息。

## 环境变量

所有环境变量使用 `AGENTCLOAK_` 前缀。

### Daemon 设置

| 变量 | 配置项 | 默认值 | 说明 |
|------|-------|-------|------|
| `AGENTCLOAK_HOST` | `daemon.host` | `127.0.0.1` | Daemon 监听地址 |
| `AGENTCLOAK_PORT` | `daemon.port` | `18765` | Daemon 监听端口 |
| `AGENTCLOAK_HTTP_CLIENT_TIMEOUT` | `daemon.http_client_timeout` | `90` | CLI / MCP 调用 daemon 的 HTTP 读取超时（秒） |
| `AGENTCLOAK_HTTP_CONNECT_TIMEOUT` | `daemon.http_connect_timeout` | `5.0` | CLI / MCP 连接 daemon 的 TCP 握手超时（秒），保持短以便死掉或远程的 daemon 快速失败 |
| `AGENTCLOAK_AUTO_START_TIMEOUT` | `daemon.auto_start_timeout` | `15.0` | 自动拉起 daemon 后等待 `/health` 的总时长（秒） |
| `AGENTCLOAK_AUTO_START_POLL_INTERVAL` | `daemon.auto_start_poll_interval` | `0.5` | 自动启动期间健康检查轮询间隔（秒） |
| `AGENTCLOAK_LOG_LEVEL` | `daemon.log_level` | `warning` | Daemon 日志级别（debug/info/warning/error） |
| `AGENTCLOAK_LOG_TO_FILE` | `daemon.log_to_file` | `false` | 将 daemon 日志镜像写入 `~/.agentcloak/logs/daemon.log` 并轮转 |
| `AGENTCLOAK_LOG_MAX_BYTES` | `daemon.log_max_bytes` | `10000000` | 单个轮转日志文件最大字节数（默认 10 MB） |
| `AGENTCLOAK_LOG_BACKUP_COUNT` | `daemon.log_backup_count` | `3` | 保留的轮转日志文件数量 |

### 浏览器设置

| 变量 | 配置项 | 默认值 | 说明 |
|------|-------|-------|------|
| `AGENTCLOAK_DEFAULT_TIER` | `browser.default_tier` | `auto` | 浏览器后端。`auto` 解析为 `cloak` |
| `AGENTCLOAK_TIER` | （别名） | -- | `DEFAULT_TIER` 的简写 |
| `AGENTCLOAK_DEFAULT_PROFILE` | `browser.default_profile` | `""` | 启动时使用的命名 profile |
| `AGENTCLOAK_PROFILE` | （别名） | -- | `DEFAULT_PROFILE` 的简写 |
| `AGENTCLOAK_VIEWPORT_WIDTH` | `browser.viewport_width` | `1280` | 浏览器视口宽度（像素） |
| `AGENTCLOAK_VIEWPORT_HEIGHT` | `browser.viewport_height` | `720` | 浏览器视口高度（像素） |
| `AGENTCLOAK_NAVIGATION_TIMEOUT` | `browser.navigation_timeout` | `30` | 页面加载超时（秒） |
| `AGENTCLOAK_NAVIGATION_TIMEOUT_SEC` | （别名） | -- | `NAVIGATION_TIMEOUT` 的别名 |
| `AGENTCLOAK_IDLE_TIMEOUT_MIN` | `browser.idle_timeout_min` | `30` | 空闲 N 分钟后自动关闭（0 = 禁用） |
| `AGENTCLOAK_STOP_ON_EXIT` | `browser.stop_on_exit` | `false` | CLI 进程退出时停止 daemon |
| `AGENTCLOAK_HEADLESS` | `browser.headless` | `true` | 浏览器无窗口运行 |
| `AGENTCLOAK_HUMANIZE` | `browser.humanize` | `true` | 启用 CloakBrowser 拟人行为（鼠标曲线、打字节奏） |
| `AGENTCLOAK_ACTION_TIMEOUT` | `browser.action_timeout` | `30000` | 操作超时（毫秒） |
| `AGENTCLOAK_BATCH_SETTLE_TIMEOUT` | `browser.batch_settle_timeout` | `2000` | 批量操作间等待页面稳定的时间（毫秒） |
| `AGENTCLOAK_MAX_RETURN_SIZE` | `browser.max_return_size` | `50000` | `/evaluate` 返回值的最大字节数（超出截断，避免 MCP token 爆掉） |
| `AGENTCLOAK_SCREENSHOT_FORMAT` | `browser.screenshot_format` | `jpeg` | 截图默认编码：`jpeg` 或无损 `png` |
| `AGENTCLOAK_SCREENSHOT_QUALITY` | `browser.screenshot_quality` | `80` | CLI 截图默认 JPEG 质量（0-100） |
| `AGENTCLOAK_MCP_SCREENSHOT_QUALITY` | `browser.mcp_screenshot_quality` | `50` | MCP 截图默认 JPEG 质量（低于 CLI 以节省 token） |
| `AGENTCLOAK_SNAPSHOT_MAX_NODES` | `browser.snapshot_max_nodes` | `80` | compact 模式 snapshot 默认节点上限（未传 `--limit`/`max_nodes` 时生效）。`--limit 0`（CLI）或 `max_nodes=0`（MCP）可重新打开完整树。仅在 compact 模式生效。 |
| `AGENTCLOAK_PROXY` | `browser.proxy` | `""` | 浏览器上游代理（例：`socks5://user:pass@host:1080`、`http://corp-proxy:3128`）。空值 = 直连。修改后需重启 daemon 才能生效。 |
| `AGENTCLOAK_DNS_OVER_HTTPS` | `browser.dns_over_https` | `false` | 为 `false`（默认）时 agentcloak 会给 Chromium 追加 `--disable-features=DnsOverHttps`，让 DNS 走系统 resolver——兼容透明 / split-horizon 代理。设为 `true` 后 Chromium 使用内置 DoH 解析器。 |
| `AGENTCLOAK_EXTRA_ARGS` | `browser.extra_args` | `[]` | 逗号分隔的额外 Chromium 命令行参数，每次启动浏览器都会追加（例：`--lang=ja-JP,--disable-blink-features=AutomationControlled`）。完全由用户控制；agentcloak 不做校验。 |

### 安全设置

| 变量 | 配置项 | 默认值 | 说明 |
|------|-------|-------|------|
| `AGENTCLOAK_DOMAIN_WHITELIST` | `security.domain_whitelist` | `[]` | 逗号分隔的允许域名列表（glob 模式）。设置后，导航到不在列表中的 domain 会被拦截并返回 `domain_blocked`。同时启用第 3 层不可信内容包裹——对已加载的非白名单页面 snapshot 自动加上 `<untrusted_web_content>` 标签。 |
| `AGENTCLOAK_DOMAIN_BLACKLIST` | `security.domain_blacklist` | `[]` | 逗号分隔的阻止域名列表（glob 模式）。导航到列表中的 domain 会被拦截。同时设置白名单时，白名单优先。 |
| `AGENTCLOAK_CONTENT_SCAN` | `security.content_scan` | `false` | 启用正则内容扫描。匹配以 `security_warnings` 出现在 snapshot 输出中（仅标记，不拦截）；action 目标元素文本若命中则拦截。 |
| `AGENTCLOAK_CONTENT_SCAN_PATTERNS` | `security.content_scan_patterns` | `[]` | 逗号分隔的正则表达式（大小写不敏感）。 |

### Bridge 设置

| 变量 | 配置项 | 默认值 | 说明 |
|------|-------|-------|------|
| `AGENTCLOAK_BRIDGE_TOKEN` | `bridge.token` | 自动生成 | Chrome 扩展配对的持久 auth token。首次启动 daemon 时自动生成并写入 `config.toml`；用 `agentcloak bridge token --reset` 轮换。 |
| `AGENTCLOAK_LOCAL_IDLE_TIMEOUT` | `bridge.local_idle_timeout` | `1800` | 切换到 `remote_bridge` tier 后本地浏览器保留多久（秒），到期自动关闭释放资源。`0` 表示永不自动关闭。 |

> [!NOTE]
> `file://`、`data:` 和 `javascript:` URL 始终被阻止，不受白名单/黑名单设置影响。详见 `docs/zh/guides/security.md` 完整的 IDPI 模型。

## 浏览器后端解析

`default_tier` / `AGENTCLOAK_DEFAULT_TIER` 值控制使用哪个浏览器后端：

| 值 | 解析为 | 后端 |
|----|-------|------|
| `auto` | `cloak` | CloakBrowser（默认） |
| `cloak` | `cloak` | CloakBrowser 隐身 |
| `playwright` | `playwright` | 标准 Playwright Chromium |
| `remote_bridge` | `remote_bridge` | RemoteBridge（通过扩展的真实 Chrome） |

> v0.2.0 移除了旧版 `patchright` 别名——请将旧的 `config.toml` 改为直接使用
> `playwright`（或 `cloak`）。

## Daemon CLI 参数

手动启动 daemon 时也可通过 CLI 参数配置：

```bash
cloak daemon start --host 0.0.0.0 --port 18765 --headed --profile my-session
```

| 参数 | 说明 |
|------|------|
| `--host` | 监听地址（覆盖配置） |
| `--port` | 监听端口（覆盖配置） |
| `--headed` | 以有头模式运行浏览器（可见窗口） |
| `--profile NAME` | 使用命名的浏览器 profile |
| `--idle-timeout MINUTES` | 空闲一段时间后自动关闭 |

## 文件系统路径

| 路径 | 用途 |
|------|------|
| `~/.agentcloak/` | 根配置目录 |
| `~/.agentcloak/config.toml` | 配置文件 |
| `~/.agentcloak/profiles/` | 保存的浏览器 profile |
| `~/.agentcloak/logs/` | Daemon 日志文件 |
| `~/.agentcloak/active-session.json` | 当前 daemon 会话信息 |
| `~/.agentcloak/resume.json` | 会话恢复数据 |
| `~/.cloakbrowser/` | CloakBrowser 二进制文件缓存 |

## 配置示例

### 最小隐身配置

```toml
[browser]
humanize = true
```

### 严格安全策略

```toml
[security]
domain_whitelist = ["*.example.com", "api.service.io"]
domain_blacklist = ["*.tracking.com"]
content_scan = true
content_scan_patterns = ["password=\\w+", "api[_-]?key=\\w+"]
```

### 自定义 daemon 端口

```toml
[daemon]
host = "0.0.0.0"
port = 19000
```

或通过环境变量设置：

```bash
export AGENTCLOAK_HOST=0.0.0.0
export AGENTCLOAK_PORT=19000
```

### 浏览器网络（代理 / DoH / 额外参数）

```toml
[browser]
# 让所有浏览器请求走住宅 SOCKS5 代理
proxy = "socks5://user:pass@residential.example:1080"

# 保持系统 DNS（默认）。设为 true 后让 Chromium 用内置 DoH。
dns_over_https = false

# 额外 Chromium 启动参数。常用于伪装区域、控制特性开关等。
extra_args = ["--lang=ja-JP", "--disable-blink-features=AutomationControlled"]
```

或通过环境变量（设置后重启 daemon）：

```bash
export AGENTCLOAK_PROXY="socks5://host:1080"
export AGENTCLOAK_DNS_OVER_HTTPS=false
export AGENTCLOAK_EXTRA_ARGS="--lang=ja-JP,--disable-blink-features=AutomationControlled"
```

> [!NOTE]
> `proxy` 只影响浏览器自身。`cloak fetch` 仍走内置的 httpcloak 本地 TLS
> 代理以保证 TLS 指纹与 CloakBrowser 一致——两条出口是有意分离的独立链路。

## 从 CLI 修改配置

`cloak config` 提供类似 git 的动词来编辑 `~/.agentcloak/config.toml`：

```bash
cloak config                                  # 列出所有配置（含来源）
cloak config list                             # 同上
cloak config get browser.proxy                # 读取单项
cloak config set browser.proxy "socks5://host:1080"
cloak config set browser.headless false browser.humanize true   # 批量赋值
cloak config add browser.extra_args "--lang=ja-JP"              # 向 list 追加
cloak config remove browser.extra_args "--lang=ja-JP"           # 从 list 删除
cloak config unset browser.proxy                                # 恢复默认
cloak config keys                                               # 列出可用 key
```

写入操作只动 `~/.agentcloak/config.toml`，不影响 env 或 default。改动
`[daemon]` 或 `[browser]` 下的项需重启 daemon 才能生效，命令会在适用时
输出 `(restart daemon to apply)` 提示。
