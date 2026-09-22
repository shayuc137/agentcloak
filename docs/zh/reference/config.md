# 配置参考

agentcloak 开箱即用，无需任何配置。所有设置都有合理的默认值，可通过配置文件或环境变量覆盖。

`AGENTCLOAK_HOME` 指定整个状态目录（默认 `~/.agentcloak`），包括配置、profile、工作空间存储、日志与运行记录。daemon 整个生命周期持有 `daemon.lock`：每个状态目录只允许一个 daemon，不依赖 PID 命名空间；不同状态目录可并行运行。隔离测试请同时设置临时 `AGENTCLOAK_HOME` **和不同的 `AGENTCLOAK_PORT`**，状态目录不隔离网络端点。客户端以实时 `/health` 获取当前 profile。升级后重启 daemon 才会启用生命周期锁。

## 优先级

设置按以下顺序解析（高优先级优先）：

1. **CLI 参数**（启动时显式提供的覆盖值）
2. **当前 profile 配置**（仅覆盖 `[browser]` / `[security]`，工作空间根目录仍使用全局配置）
3. **环境变量**（`AGENTCLOAK_*`）
4. **全局配置文件**（`~/.agentcloak/config.toml`）
5. **内置默认值**

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
isolation = "shared"
workspace_roots = []
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

## 相关状态文件

Overlay 选择器和 cookie 恢复快照属于运行状态，并非配置项。活动 profile 会把它们分别存为 `~/.agentcloak/profiles/<name>/hide.json` 和 `cookies-snapshot.json`。无 profile 时，隐藏选择器仅在当前 session 生效，cookie fallback 为 `~/.agentcloak/cookies-snapshot.json`。使用 `cloak hide add/remove/list` 与 `cloak cookies export/restore` 管理，不要把它们写进 `config.toml`。

## Profile 级 config overlay

某个 profile 需要独立的 browser / security 配置时，把 `config.toml` 直接放到该 profile 目录里：

```
~/.agentcloak/profiles/<name>/config.toml
```

daemon 每次以该 profile 启动（`cloak daemon start --profile <name>`、`cloak profile launch <name>` 或 `AGENTCLOAK_PROFILE=<name>`）时会读这份文件，**只覆盖** `[browser]` 与 `[security]` 两段；其余段（`[daemon]`、`[bridge]` 里的端口、bridge token 等属于进程级/主机级设置）仍走全局配置，避免不同 profile 抢端口。缺文件或缺段都会静默跳过。

```toml
# ~/.agentcloak/profiles/scraper/config.toml
[browser]
humanize = false
proxy = "socks5://scraper-egress:1080"

[security]
domain_whitelist = ["*.target.com"]
```

`[browser]` / `[security]` 的有效优先级为：**CLI 参数 > profile 配置 > 环境变量 > 全局配置 > 默认值**。其他段按全局优先级解析。

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

写入操作只动 `~/.agentcloak/config.toml`，不影响 env 或 default。请求期默认值
（`browser.screenshot_format`、`screenshot_quality`、`snapshot_max_nodes`、
`batch_settle_timeout` 和 `max_return_size`）会在下一次相关 daemon 请求时重新
加载。`[daemon]` / `[browser]` 中影响进程或浏览器启动的配置仍需重启；批量
修改包含任一启动期配置时会输出 `(restart daemon to apply)`。

## 工作空间隔离

默认 `browser.isolation = "shared"`，继续共享 profile 的登录数据。显式改为 `workspace` 并重启 daemon 后，每个工作空间使用独立浏览器 context：

```bash
cloak config set browser.isolation workspace
cloak config add browser.workspace_roots ~/work/assistant
cloak daemon stop
cloak daemon start
```

`AGENTCLOAK_ISOLATION` 可覆盖全局模式。运行中的 daemon 保持启动时的模式；`/health` 返回实际生效的 `isolation` 和调用方的 `workspace_id`。`workspace_roots` 是客户端读取的目录列表，建议使用绝对路径或 `~` 路径。根目录覆盖所有子目录，多个根匹配时取最长路径。客户端根目录应放在全局配置中，不放在 profile 配置中。

工作空间按以下优先级识别：`--workspace PATH`、`AGENTCLOAK_WORKSPACE`、配置根目录、worktree 所属的 Git 仓库、无 Git 时的当前目录。规范化完整路径的哈希可避免同名目录碰撞。未指定根目录时，不同的非 Git 子目录分别视为独立空间。Git worktree 共享所属空间的存储，默认 session 则各自独立；分别指定 `--workspace` 也能隔离 worktree 的存储。

两种模式都使用 `(workspace_id, session_id)` 标识页面会话。`session list` 和 `session close` 只作用于调用方的工作空间。`workspace` 模式中，同空间各 session 共享 cookie、localStorage 和 IndexedDB，其他空间使用独立存储。`shared` 保留原有 profile 存储；切换模式不会将它复制到工作空间中。

工作空间在最后一个 session 关闭、空闲回收或 daemon 正常退出时，将状态保存到 `~/.agentcloak/workspaces/<工作空间与-profile-哈希>/storage.json`，再次打开时恢复；POSIX 文件权限为 0600。默认 cookie 导出/恢复快照也放在这个目录，仍可显式指定文件。浏览器崩溃可能丢失上次保存后的变更；sessionStorage、页面 DOM、历史、缓存和 service worker 不会恢复。这是存储和页面隔离，不能防范原始 CDP 或文件系统访问。浏览器进程、启动参数和代理仍共享；存在其他活跃 session 时拒绝切换 tier/profile。RemoteBridge 使用用户现有浏览器存储，因此明确拒绝 workspace 模式。

workspace 模式在没有命名 profile 时也会持久化。`profile create --from-current` 会按显式请求导出 profile 种子；在 workspace 模式启动这个 profile，不会把其中的 cookie/localStorage 灌入每个空间。需要迁移 cookie 时，显式指定导出/恢复文件。

`daemon start --log-level LEVEL` 可为单个进程覆盖 `daemon.log_level`；`browser.action_timeout` 同时约束会话等锁、截图和快照执行，见[恢复说明](../guides/recovery.md)。

安装可选 `discovery` 后，可从局域网访问的监听地址会在 HTTP 就绪后广播；仅回环地址不广播，发现失败不阻止 HTTP。详见 [mDNS 服务广播](../guides/remote-bridge.md#mdns-服务广播可选)。
