# CLI 参考

agentcloak 提供两个等效的 CLI 入口：`agentcloak` 和 `cloak`（简写）。以下示例统一使用 `cloak`。

## 输出约定

v0.2.0 起 CLI 是**文本优先**的。stdout 本身就是答案；stderr 承载提示和错误；exit code `0` 成功 / `1` 业务失败 / `2` 用法错误。

示例：

```text
$ cloak navigate https://example.com
https://example.com/ | Example Domain

$ cloak snapshot
# Example Domain | https://example.com/ | 8 nodes (1 interactive) | seq=2
  heading "Example Domain" level=1
  [1] link "Learn more" href="https://iana.org/domains/example"

$ cloak click 99
Error: Element [99] not in selector_map (1 entries)
  -> run 'snapshot' to refresh the selector_map, or re-snapshot if the page changed
```

脚本/jq 流水线/MCP 风格消费方需要旧 envelope：

```bash
# --json flag（任意位置）
cloak --json snapshot | jq -r '.data.tree_text'

# AGENTCLOAK_OUTPUT 环境变量（CI / wrapper 无需改命令行）
AGENTCLOAK_OUTPUT=json cloak snapshot
```

`--json` 生效时的 envelope shape：

```json
{"ok": true, "seq": 3, "data": {...}}
{"ok": false, "error": "error_code", "hint": "description", "action": "suggested next step"}
```

## 全局参数

| 参数 | 效果 |
|------|------|
| `--json` | 整个命令切回 JSON envelope 输出 |
| `--pretty` | 缩进 JSON 输出（无 `--json` 时空操作并 stderr 警告） |
| `--verbose` / `-v` | 提高日志等级（`-v` info，`-vv` debug） |
| `--version` | 打印版本并退出 |
| `AGENTCLOAK_OUTPUT=json` 环境变量 | 等同 `--json`，无需改命令行 |

## 导航与观察

### navigate

导航浏览器到指定 URL。

```bash
cloak navigate URL [--timeout SECONDS] [--snap] [--snapshot-mode MODE]
```

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `--timeout` | `30` | 等待页面加载的最大秒数 |
| `--snap`（别名 `--snapshot`） | 关闭 | 附带 compact snapshot（省一次往返） |
| `--snapshot-mode` | `compact` | `--snap` 启用时的 snapshot 模式（`compact` 或 `accessible`） |

### snapshot

获取带有 `[N]` 元素引用的无障碍树。

```bash
cloak snapshot [--mode MODE] [--limit N] [--focus N] [--offset N] [--frames] [--diff] [--selector-map]
```

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `--mode` | `compact` | `compact`（默认）、`accessible`、`content`、`dom` |
| `--limit`（别名 `--max-nodes`） | `0` | 在 N 个节点后截断（0 = 不限制） |
| `--focus` | `0` | 展开元素 `[N]` 周围的子树 |
| `--offset` | `0` | 从第 N 个元素开始输出（分页） |
| `--frames` | 关闭 | 包含 iframe 内容 |
| `--diff` | 关闭 | 标记与上一次 snapshot 相比的变更 |
| `--selector-map` | 关闭 | 输出原始 selector_map（调试/脚本场景） |

输出以 header 行开头：

```text
# <title> | <url> | <total_nodes> nodes (<interactive> interactive) | seq=<n>
```

### screenshot

截取当前页面的屏幕截图。

```bash
cloak screenshot [--output FILE] [--full-page] [--format FORMAT] [--quality N]
```

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `--output` | 自动放在系统临时目录（`tempfile.gettempdir()`） | 保存到文件；stdout 打印文件路径 |
| `--full-page` | 关闭 | 捕获完整可滚动页面 |
| `--format` | `jpeg` | `jpeg` 或 `png` |
| `--quality` | `80` | JPEG 质量 0-100（PNG 时忽略） |

> [!TIP]
> **PNG 和 JPEG 的选择：**
> - `--format png` — UI 设计验证、OCR、视觉模型。无损质量避免 JPEG 伪影干扰文字识别或像素级对比。
> - `--format jpeg`（默认）— 版面检查、页面状态验证。体积小 4-10 倍，像素精度要求不高时够用。
>
> MCP 工具默认 JPEG 质量 50（可通过 `browser.mcp_screenshot_quality` 配置），
> 控制 token 预算。CLI 默认质量 80。

### resume

获取会话状态用于上下文恢复。

```bash
cloak resume
```

返回当前 URL、打开的标签页、最近 5 次操作、捕获状态和隐身等级。

## 交互

所有交互命令都接受位置参数（`cloak click 5`）或 `--index N` / `-i N`。多数命令还接受第二个位置参数（`cloak fill 5 "query"`）。

加 `--snap` 到任意交互命令，可附带 compact snapshot。

### click

通过 `[N]` 引用点击元素。

```bash
cloak click N [--snap]
cloak click --index N [--snap]
cloak click --x X --y Y           # 坐标 fallback
```

### fill

清空输入框并设置值。

```bash
cloak fill N "value" [--snap]
cloak fill --index N --text "value" [--snap]
```

### type

逐字符输入文本（触发按键事件）。

```bash
cloak type N "value" [--snap]
```

### press

按下键盘按键或组合键。

```bash
cloak press KEY [N] [--snap]
cloak press --key KEY [--index N] [--snap]
```

按键名称使用 Playwright 语法：`Enter`、`Tab`、`Escape`、`Control+a`、`Shift+ArrowDown`。

### scroll

滚动页面。

```bash
cloak scroll DIRECTION [--snap]
cloak scroll --direction DIRECTION
```

方向：`up` 或 `down`。

### hover

悬停在元素上。

```bash
cloak hover N [--snap]
```

### select

选择下拉选项。

```bash
cloak select N --value "option" [--snap]
```

## 内容与网络

### js evaluate

在页面上下文中执行 JavaScript。

```bash
cloak js evaluate "expression"
```

scalar 结果（string/number/boolean）直接输出裸值。对象和数组打印为 pretty JSON。

### fetch

使用浏览器的 cookie 和 user agent 发起 HTTP 请求。响应 body 走 stdout；status/headers 走 stderr。

```bash
cloak fetch URL [--method METHOD] [--body BODY] [--headers-json JSON]
```

### network requests

列出最近的网络请求。

```bash
cloak network requests [--since SEQ]
```

使用 `--since last_action` 查看最近一次操作触发的请求。

### network console

列出控制台消息。

```bash
cloak network console [--since SEQ]
```

## 对话框处理

```bash
cloak dialog status                # 检查是否有待处理对话框
cloak dialog accept [--text "reply"]
cloak dialog dismiss
```

## 等待

```bash
cloak wait --selector "CSS_SELECTOR"
cloak wait --url "**/dashboard"
cloak wait --load networkidle
cloak wait --js "document.readyState === 'complete'"
cloak wait --ms 2000
```

| 参数 | 说明 |
|------|------|
| `--selector` | 等待 CSS 选择器出现 |
| `--url` | 等待 URL 匹配（glob 模式） |
| `--load` | 等待加载状态（`load`、`domcontentloaded`、`networkidle`） |
| `--js` | 等待 JS 表达式返回真值 |
| `--ms` | 休眠 N 毫秒 |
| `--timeout` | 最大等待时间（毫秒，默认 30000） |

### 常用组合

```bash
# 等待 Web Font 加载完再截图
cloak wait --js "document.fonts.ready.then(() => true)"
cloak screenshot --format png

# 等待所有网络请求结束（SPA 水合、懒加载数据）
cloak wait --load networkidle

# 等待特定 API 数据就绪后提取
cloak wait --js "window.__DATA_LOADED === true"

# 组合：导航 → 等待网络空闲 + 字体加载 → 全页截图
cloak navigate "https://example.com"
cloak wait --load networkidle
cloak wait --js "document.fonts.ready.then(() => true)"
cloak screenshot --format png --full-page
```

> [!TIP]
> `--js` 表达式必须返回真值。对于 Promise（如 `document.fonts.ready`），
> 用 `.then(() => true)` 包裹。

## 文件上传

```bash
cloak upload --index N --file /path/to/file [--file /path/to/another]
```

## Frame 管理

```bash
cloak frame list
cloak frame focus --name "frame-name"
cloak frame focus --url "partial-url"
cloak frame focus --main
```

## 网页逆向

基于 CDP 的检视与操纵能力。每项能力在首次使用时才惰性 enable 对应的 CDP 域，从不做逆向的会话零开销。所有命令在三种后端（CloakBrowser、Playwright、RemoteBridge）上都可用。

### Init script

注入在每次导航时先于页面脚本运行的 JavaScript——给 `fetch` / `XHR` / `JSON.parse` 打补丁的 hook 点。

```bash
cloak script add "JS"                 # 注入原始 JS，打印一个标识符
cloak script add --preset fetch       # 内置 hook：fetch|xhr|json_parse|crypto|timing
cloak script remove ID
cloak script list
```

预设会把拦截到的调用打到 `cloak console`。

### 网络路由拦截

按 URL 模式拦截请求。规则跨导航持续，并在新标签页上重放。

```bash
cloak route add "**/api/*" --action abort
cloak route add "**/track" --action fulfill --status 204 --content-type application/json --body "{}"
cloak route add "*" --action continue --resource-type xhr --method POST
cloak route remove "**/api/*"         # 省略 pattern 则清空全部规则
cloak route list
```

### 额外 HTTP header

```bash
cloak emulation headers -H "Authorization: Bearer TOKEN" -H "X-Requested-With: XMLHttpRequest"
cloak emulation headers               # 不带 -H 则清空所有覆盖
```

### GraphQL

通过浏览器会话执行（cookie + 安全域名检查）。

```bash
cloak graphql introspect https://api.example.com/graphql
cloak graphql query https://api.example.com/graphql "query { me { id } }" --variables '{"id": 1}'
cloak graphql query URL QUERY -H "Authorization: Bearer TOKEN"
```

### 流式监控（WebSocket + SSE）

捕获 `network requests` 看不到的流量。按单调 seq 分页缓冲。

```bash
cloak ws list                          # 追踪的 WebSocket 连接
cloak ws messages [--since SEQ]        # → 发送、← 接收 的帧
cloak sse messages [--since SEQ]       # Server-Sent Events
```

### 调试器

设断点、单步、读调用栈和作用域。该域惰性 enable；暂停期间页面操作返回 `debugger_paused`，直到 `resume` / `step`。

```bash
cloak debugger enable
cloak debugger breakpoint-set "main\.js" 42 --condition "x > 1"   # URL 正则 + 从 0 开始的行号
cloak debugger breakpoint-remove ID
cloak debugger breakpoint-list
cloak debugger xhr-set "/api/login"    # 在匹配的 XHR 上断下（省略 pattern = 所有 XHR）
cloak debugger xhr-remove "/api/login"
cloak debugger paused-info             # 暂停原因 + 调用栈（callFrameId 在方括号里）
cloak debugger step --type over        # over | into | out
cloak debugger resume
cloak debugger scope-variables OBJECT_ID
cloak debugger evaluate CALL_FRAME_ID "expr"
cloak debugger scripts                 # 已解析脚本（id、URL、source-map 标记）
cloak debugger script-source SCRIPT_ID
cloak debugger search SCRIPT_ID "query" --regex --case-sensitive
cloak debugger skip-pauses true        # 忽略所有断点 / debugger;（反反调试）
```

### Source map

将编译后的位置反查回原始源。需要先 enable 调试器。

```bash
cloak sourcemap list                   # 声明了 sourceMapURL 的脚本
cloak sourcemap get SCRIPT_ID          # 下载 + 解析；元数据摘要
cloak sourcemap lookup SCRIPT_ID --line N --column N   # 编译位置 → 原始 source:line:col
cloak sourcemap sources SCRIPT_ID      # 原始源文件路径
cloak sourcemap source-content SCRIPT_ID SOURCE_PATH
```

### 性能分析

JS 代码覆盖率、CPU 性能分析、运行时指标和堆内存快照。

```bash
cloak profiler coverage-start              # 开始记录函数级覆盖率
cloak profiler coverage-stop               # 停止记录
cloak profiler coverage-get                # 每个脚本的摘要（函数总数/已覆盖/百分比）
cloak profiler coverage-get --script-id ID # 单个脚本的逐函数详情
cloak profiler cpu-start                   # 开始 CPU 采样
cloak profiler cpu-stop                    # 停止并显示按耗时排名的热点函数
cloak profiler cpu-stop --output profile.cpuprofile  # 保存原始 profile（可在 DevTools 中打开）
cloak profiler heap-snapshot --output snap.heapsnapshot  # V8 堆内存转储到文件
cloak performance metrics                  # DOM 节点数、JS 堆大小、布局次数
```

## 捕获与 spell

```bash
cloak capture start
cloak capture stop
cloak capture status
cloak capture export --format har > traffic.har
cloak capture export --format json
cloak capture analyze [--domain example.com]
cloak capture clear

cloak spell list
cloak spell info NAME
cloak spell run NAME [key=value ...]
cloak spell scaffold SITE COMMAND
```

`capture export` 把裸 HAR/JSON 写到 stdout——pipe 到文件。`spell run` 直接打印 spell 返回值（不裹 envelope）。

## Profile 管理

```bash
cloak profile create NAME [--from-current]
cloak profile list
cloak profile launch NAME
cloak profile delete NAME
```

## 标签页管理

```bash
cloak tab list                    # git-branch 风格：* 标记 active
cloak tab new [--url URL]
cloak tab close --tab-id N
cloak tab switch --tab-id N
```

## Bridge 命令

```bash
cloak bridge claim --tab-id N
cloak bridge claim --url "dashboard"
cloak bridge finalize --mode close        # 关闭 agent 标签页
cloak bridge finalize --mode handoff      # 保留标签页给用户
cloak bridge finalize --mode deliverable  # 将 group 重命名为 "results"
cloak bridge token                        # 打印持久化的 auth token
cloak bridge token --reset                # 轮换 token
```

`cloak bridge token` 把裸 token 写到 stdout——方便 pipe 给其他工具。

## Cookie 管理

```bash
cloak cookies export                              # 当前浏览器所有 cookie
cloak cookies export --url https://example.com    # 只导出匹配该 URL 的 cookie
cloak cookies import -c '[{"name":"token","value":"abc","domain":".example.com","path":"/"}]'
```

`cookies export` 输出 `domain | name=value` 行（每个 cookie 一行）——加上 domain
列让 agent grep 时能分辨每个 cookie 属于哪个站点。建议用 `--url` 把导出范围限定
到单个 domain；不加过滤会把当前浏览器里**所有**站点的会话一并吐出来，包括
agent 任务无关的个人账号。`cookies import` 接受结构化 JSON，保留 httpOnly cookie。

## Daemon 管理

```bash
cloak daemon start [--host HOST] [--port PORT] [--headed] [--profile NAME]
cloak daemon stop
cloak daemon status                # tier | browser status | seq
```

## 配置

```bash
cloak config                       # 等同 config list
cloak config list                  # key = value (source) — 类似 git config -l
cloak config get <key>             # 读取单个值
cloak config set <key> <val...>    # 设置标量或替换列表（批量: k1 v1 k2 v2）
cloak config add <key> <val...>    # 追加到列表类型的 key
cloak config remove <key> <val>    # 从列表类型的 key 移除
cloak config unset <key>           # 恢复默认值
cloak config keys                  # 列出所有可设置的 key
```

key 使用点分格式（如 `browser.proxy`、`browser.extra_args`）。类型由配置 schema 推断——`add`/`remove` 只对列表字段生效。修改 browser/daemon 配置后会提示重启。

详见[配置参考](config.md)了解所有可用 key 和环境变量。

## 诊断

```bash
cloak doctor                       # 精简摘要 + 运行状态（2 行）
cloak doctor --detail              # 详细每项检查 [ok]/[fail] 行
cloak doctor --fix                 # 尝试进程内修复（binary 下载、数据目录）
cloak doctor --fix --sudo          # 用 sudo 执行合成的系统命令

cloak cdp endpoint                 # jshookmcp / 其他 CDP 工具用的裸 ws:// URL
```

`doctor` 任意检查失败时 exit code 为 `1`，方便 shell 脚本组合。
