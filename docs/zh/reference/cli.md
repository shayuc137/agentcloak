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
Error [element_not_found]: Element [99] not in selector_map (1 entries)
  -> run 'snapshot' to refresh the selector_map, or re-snapshot if the page changed
```

脚本和 jq 流水线可启用结构化输出：

```bash
# --json flag（任意位置）
cloak --json snapshot | jq -r '.data.tree_text'

# AGENTCLOAK_OUTPUT 环境变量（CI / wrapper 无需改命令行）
AGENTCLOAK_OUTPUT=json cloak snapshot
```

`--json` 生效时的 envelope shape：

```json
{"ok": true, "seq": 3, "data": {...}}
{"ok": false, "error": {"code": "error_code", "message": "description"}, "hint": "description", "action": "suggested next step"}
```


失败时 stdout 只输出一份 JSON envelope，stderr 输出 `Error [code]: message`。缺少参数和本地校验失败也遵循此约定。`js evaluate` 抛出异常或 Promise 拒绝会失败；正常返回以 `Error:` 开头的字符串仍属于成功数据。诊断失败可在 `data` 中附带检查结果。

| `error.code` | 含义 |
|--------------|------|
| `invalid_request` | CLI 参数错误或 daemon 请求校验失败 |
| `command_failed` | 本地命令校验失败 |
| `command_aborted` | 命令被中断或取消 |
| `internal_error` | CLI 或 daemon 的未预期异常 |
| `config_error` | 配置键或值无效 |
| `doctor_failed`、`bridge_check_failed` | 环境或 bridge 检查失败，JSON 附带诊断 `data` |
| `skill_uninstall_failed` | skill 文件操作失败 |
| `daemon_unreachable`、`daemon_timeout` | daemon 连接失败或请求超时 |
| `daemon_invalid_response`、`daemon_request_failed` | 响应体无效或 daemon 请求失败 |
| `evaluate_failed` | 本地后端的 JavaScript 语法或运行时异常 |
| `cdp_timeout`、`cdp_call_failed` | 原生 CDP 超时或协议错误 |

其他领域错误码（例如 `element_not_found`）保持原值。直接调用 daemon 和 MCP 时，错误仍使用字符串 `error` 加 `hint`、`action`；嵌套的 `error.code/message` 属于 CLI JSON 输出。

## 全局参数

| 参数 | 效果 |
|------|------|
| `--workspace PATH` | 指定工作空间根目录，覆盖子目录；支持放在命令前后 |
| `--session ID` | 将本次调用绑定到命名浏览器会话，可放在命令前后 |
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

简单的 `#fragment` 会等待最多 3 秒，直到同 id 元素出现，再滚动到该元素，覆盖 SPA 延迟渲染锚点的场景。未命中时导航仍成功，并输出 `[anchor] not found`。Hashbang 路由和包含 `=`、`&` 或 `/` 的参数型 fragment 交给应用处理。

### snapshot

获取带有 `[N]` 元素引用的无障碍树。`compact` 和 `accessible` 都包含无障碍树暴露的 `button`/`menuitem` 及可聚焦自定义元素，包括 `tabindex="0"` 和 `tabindex="-1"`。被 AX 忽略的节点仍不显示；菜单展开后重新 snapshot，才能获取菜单项引用。

```bash
cloak snapshot [--mode MODE] [--selector CSS] [--find TEXT] [--limit N] [--focus N] [--offset N] [--frames] [--diff] [--hide CSS] [--keep-overlays]
```

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `--mode` | `compact` | `compact`（默认）、`accessible`、`content`、`dom` |
| `--selector`（别名 `--within`、`-s`） | 无 | 将无障碍树限制到主文档中的 CSS 选择器范围 |
| `--limit`（别名 `--max-nodes`） | `0` | 在 N 个节点后截断（0 = 不限制） |
| `--focus` | `0` | 展开元素 `[N]` 周围的子树 |
| `--offset` | `0` | 从第 N 个元素开始输出（分页） |
| `--frames` | 关闭 | 包含 iframe 内容 |
| `--diff` | 关闭 | 标记与上一次 snapshot 相比的变更 |
| `--selector-map` | 关闭 | 输出原始 selector_map（调试/脚本场景） |
| `--hide` | 无 | 本次 snapshot 隐藏的逗号分隔 CSS 选择器 |
| `--keep-overlays` | 关闭 | 本次 snapshot 显示持久、一次性和 `[data-cloak-hide]` overlay |

`--selector` 会先裁剪树，再分配 `[N]` 引用，因此引用和输出都只覆盖选中的子树。它不能与 `--frames` 或 `--mode dom` 组合使用。

输出以 header 行开头：

```text
# <title> | <url> | <total_nodes> nodes (<interactive> interactive) | seq=<n>
```

### viewport

```bash
cloak viewport set 2560x1440 --dpr 2
cloak screenshot --viewport 1024x768 --dpr 2 --output compact.png
```

`viewport set` 只调整当前 session 的页面，不导航、不丢登录态。省略 `--dpr` 会保留当前设备像素比，DPR 必须为有限正数。截图覆盖是一次性的，成功、失败或取消后都会恢复原尺寸与 DPR，包括此前 raw CDP 设置的值。宽高使用 CSS 像素，为不超过 16384 的正整数；600×400 的视口在 DPR 2 下生成 1200×800 的图片。`--dpr` 也可单独用于截图。

### emulate

```bash
cloak emulate --color-scheme dark --reduced-motion
cloak emulate --color-scheme light --no-reduced-motion
cloak emulate --pointer coarse
cloak emulate --pointer fine
cloak emulate
cloak emulate reset
```

本地 Playwright 和 CloakBrowser 的深浅色、减少动画支持有头和无头模式。指针模拟要求 `browser.headless=false`（可使用 Xvfb）；无头请求会在应用任何设置前失败，因为 Chromium 无法可靠恢复桌面指针基线。`coarse` 启用一个触摸点，`fine` 关闭触摸模拟；这不会改变 User-Agent 或启用移动端视口布局。

省略的选项保留已有覆盖。不带选项时显示当前覆盖（`null` 表示浏览器默认值）。设置应用于当前会话已有和新建的标签页；弹窗在登记后继承，最早执行的脚本可能先于模拟设置。设置跨导航保留，会话关闭或 daemon 重启后清除，不影响其他会话。`reset` 清除这些覆盖，保留视口、DPR 和 HTTP headers，不能与设置选项混用。RemoteBridge 对修改返回 `unsupported_operation`。

HTTP 使用 `POST /emulation`，字段为可选的 `color_scheme`、`reduced_motion`、`pointer`、`reset`；MCP 使用同字段的 `agentcloak_emulate`。视口 DPR 使用 `POST /viewport` / `agentcloak_viewport`，临时截图 DPR 使用 `GET /screenshot` / `agentcloak_screenshot`。

### screenshot

截取当前页面的屏幕截图。

```bash
cloak screenshot [--output FILE] [--viewport WIDTHxHEIGHT] [--dpr RATIO] [--full-page] [--format FORMAT] [--quality N] [--wait-for CSS] [--hide CSS] [--keep-overlays]
```

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `--output` | 自动放在系统临时目录（`tempfile.gettempdir()`） | 保存到文件；`.png` 选择 PNG，`.jpg`/`.jpeg` 选择 JPEG |
| `--viewport` | 当前页面 | 一次性 `WIDTHxHEIGHT`，截图后恢复 |
| `--dpr` | 当前页面 | 一次性设备像素比，截图后恢复 |
| `--full-page` | 关闭 | 捕获完整可滚动页面 |
| `--format` | 输出后缀，其次为 `browser.screenshot_format`（`jpeg`） | 显式覆盖为 `jpeg` 或 `png`；必须与已识别后缀一致 |
| `--quality` | `80` | JPEG 质量 0-100（PNG 时忽略） |
| `--wait-selector` | 无 | 截图前等待 CSS 选择器可见 |
| `--wait-for` | 无 | 截图前执行标准的可见选择器等待；超时会短路且不写文件 |
| `--wait-timeout` | `browser.action_timeout` | 选择器等待超时（毫秒） |
| `--hide` | 无 | 本次截图隐藏的逗号分隔 CSS 选择器 |
| `--keep-overlays` | 关闭 | 本次截图显示持久、一次性和 `[data-cloak-hide]` overlay |

> [!TIP]
> **PNG 和 JPEG 的选择：**
> - `-o page.png` — UI 设计验证、OCR、视觉模型。无损质量避免 JPEG 伪影干扰文字识别或像素级对比。
> - `-o page.jpg` — 版面检查、页面状态验证。体积小 4-10 倍，像素精度要求不高时够用。
>
> 已识别的输出后缀无需 `--format` 即可选择编码。未知的非空后缀会先给出
> warning，再回退到实时 `browser.screenshot_format`；无后缀路径会安静回退。
> MCP 的 JPEG 质量默认 50（可通过 `browser.mcp_screenshot_quality` 配置），
> CLI 默认质量 80。

### diff screenshot

比较本地基线与另一个本地图像，或与实时页面的新 PNG 截图比较。

```bash
cloak diff screenshot BASELINE [--current FILE] [--threshold 0..255] [--output DIFF.png]
```

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `--current` | 实时页面 | 本地当前图像；省略时从活动浏览器捕获 PNG |
| `--threshold` | `0` | 忽略小于或等于此值的逐通道差异 |
| `--output` | 无 | 写入 RGBA 差异图，用红色标出变化像素 |

文本输出为稳定的单行格式：

```text
diff 12/921600 pixels (0.001302%) | max_delta=41 | 1280x720 | threshold=0
```

`--json` 还会返回精确比例和百分比、尺寸、阈值、最大通道差值、基线和当前图像路径，以及可选输出路径。像素存在差异时仍返回退出码 0；通过或失败规则由 DOS 或 CI 决定。

### resume

获取会话状态用于上下文恢复。

```bash
cloak resume
```

返回当前 URL、打开的标签页、最近 5 次操作、捕获状态和隐身等级。

## 交互

所有交互命令都接受位置参数（`cloak click 5`）或 `--index N` / `-i N`。多数命令还接受第二个位置参数（`cloak fill 5 "query"`）。

加 `--snap` 到任意交互命令，可附带 compact snapshot。

位置引用同时接受 `12` 和 `'[12]'`；zsh 等 shell 下需为方括号引用加引号，避免通配符展开。

### click

通过 `[N]` 引用点击元素。

```bash
cloak click N [--snap]
cloak click --index N [--snap]
cloak click --x X --y Y           # 坐标 fallback
cloak click N --force             # 一次性的单左击 DOM fallback
```

已知 overlay 优先用 `cloak hide add CSS` 加入隐藏规则，重新 snapshot 后正常点击；隐藏也会清理截图和 snapshot 输出。`--force` 用于未知的一次性遮挡，会对解析出的 DOM 元素调用 `click()`，绕过坐标命中测试。它仅支持单左击；与非默认 `--button` 或 `--click-count` 组合会返回 `invalid_argument`。

`cloak click N --click-count 2` 执行双击。

### fill

清空输入框并设置值。

```bash
cloak fill N "value" [--snap]
cloak fill --index N --text "value" [--snap]
```

`fill` 使用兼容前端框架的 value setter。RemoteBridge 会先调用 input/textarea/select
的原生 prototype setter，再依次冒泡 `input` 和 `change`，React/Vue 受控字段能收到更新。

### type

逐字符输入文本（触发按键事件）。

```bash
cloak type N "value" [--snap]
```

### press

修饰键别名大小写不敏感：`Ctrl` → `Control`、`Cmd`/`Command` → `Meta`、`Opt`/`Option` → `Alt`，例如 `cloak press Ctrl+Enter`。

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
cloak hover --at 100,200
cloak hover '[12]' --offset 10,-5
```

`--offset` 相对元素中心偏移；`--at` 为视口绝对坐标。

`--find TEXT` 在分页前按大小写不敏感的子串匹配可访问名称、描述或值，保留匹配节点的祖先与子树，返回的 `[N]` 可直接操作。支持 compact/accessible/content 和 CSS 范围；不支持与 `--frames` 或 DOM 模式组合。

`click`、`fill`、`hover` 支持以 `--selector CSS` 替代引用，要求在主文档中唯一匹配，且不影响已有引用。例如 `cloak fill --selector '#email' --text 'user@example.com'`。不能与引用或绝对坐标组合；hover 仍支持相对偏移。

### drag

```bash
cloak drag '[4]' '[5]' --steps 12
cloak drag --from 100,200 --to 300,400 --steps 12
```

拖拽使用浏览器真实指针输入，包括按下、移动和松开。选择两个元素引用，或同时指定起终点坐标。

`--hold MS` 在按下后停留，`--duration MS` 安排移动步骤的时间；默认均为零，范围 0–60000 ms，仍受动作超时限制。`--sample JS` 在每一步移动后求值，返回步骤号、经过毫秒数和值。`--steps`（1–1000）限定采样数；采样开销可能延长请求的持续时间。失败或取消均释放鼠标。

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
cloak js evaluate --file probe.js          # 多行 UTF-8 脚本，无需处理 shell 引号
cloak js evaluate --preset vue_inspect    # 运行逆向 preset 而非自己写 JS
```

scalar 结果（string/number/boolean）直接输出裸值。对象和数组打印为 pretty JSON。
行内代码、`--file` 和 `--preset` 三选一。执行失败时会返回真实异常消息和
首个有效源码/栈位置，并限制在 400 字符内，避免页面用超长栈占满 agent 上下文。

`--preset` 运行一段预置的逆向 JS（强制在 main world 执行，所以 JS 参数留空），返回可直接 parse 的 JSON：

| Preset | 输出 |
|--------|------|
| `vue_inspect` | Vue 2/3 组件的 `$data` / props / method / computed 键名 |
| `react_inspect` | React 组件树（名称 + props/state 键名，限制深度） |
| `jwt_decode` | 扫描 cookies / localStorage / sessionStorage 中的 JWT，解码 header + payload |
| `cookie_parse` | 结构化的 `document.cookie`（name/value） |
| `storage_dump` | 完整导出 localStorage + sessionStorage |

拼错 preset 名会返回 `unknown_preset` 错误并列出可用名称。

### fetch

使用浏览器的 cookie 和 user agent 发起 HTTP 请求。响应 body 走 stdout；status/headers 走 stderr。

```bash
cloak fetch URL [--method METHOD] [--body BODY] [--headers-json JSON]
```

### network

列出最近的网络请求。

```bash
cloak network [--since SEQ] [--pending] [--filter GLOB]
```

使用 `--since last_action` 查看最近一次操作触发的请求。

`--pending` 列出当前 session 所属标签页内仍在进行的请求，包含 URL、方法、资源类型、已收到的响应状态和经过毫秒数。收到响应头不代表结束：SSE 会持续 pending 到连接关闭。`--filter GLOB` 过滤 URL（`*` 跨越斜线），可与 `--since` 组合。pending 观测要求本地后端，RemoteBridge 明确返回 `unsupported_operation`。

### console show

列出控制台消息。

```bash
cloak console show [--since SEQ]
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

# 简单 SPA 锚点会轮询最多 3 秒并滚动到目标
cloak navigate "https://example.com/settings#billing"
cloak screenshot --wait-for "#billing"

# 组合：导航 → 等待网络空闲 + 字体加载 → 全页截图
cloak navigate "https://example.com"
cloak wait --load networkidle
cloak wait --js "document.fonts.ready.then(() => true)"
cloak screenshot --format png --full-page
```

锚点未命中不会让导航失败，并输出 `[anchor] not found`。Hashbang 路由和包含 `=`、`&` 或 `/` 的参数型 fragment 会跳过锚点处理。

> [!TIP]
> `--js` 表达式必须返回真值。对于 Promise（如 `document.fonts.ready`），
> 用 `.then(() => true)` 包裹。

## 文件上传

```bash
cloak upload --index N --file /path/to/file [--file /path/to/another]
cloak upload --file /path/to/file                  # 自动查找隐藏的 file input
cloak upload --file /path/to/file --nth 1          # 选第 2 个 file input
```

带 `--index` 时定位指定的 snapshot `[N]` 引用。省略 `--index`，daemon 会自动查找页面上所有 `input[type=file]`——包括 drag-drop 上传组件（Dropzone、react-dropzone、Ant Upload）藏在 a11y tree 之外的 `display:none` 输入——并附加到 `--nth` 那个（从 0 开始，默认 0）。响应中会报告 `candidates_count` 和 `used_nth`，选错时可以换个 `--nth` 重发。找不到任何 file input 时返回 `no_file_input_found`；`--nth` 超出范围返回 `file_input_index_out_of_range`。

## 下载

```bash
cloak download url URL [--output DIR]              # 直接下载，带浏览器 cookie（受 SSRF 检查）
cloak download wait [--output DIR] [--timeout S]   # 阻塞等待下一个点击触发的下载
cloak download wait-click --index N [--force]      # 点击 [N] 并等待下载，一次完成
cloak download list                                # 本次会话已保存的下载
```

文件保存在 daemon 主机上（默认系统临时目录）。`wait-click` 先 arm download waiter，点击 `[N]`，再等待完成，一次请求搞定——按钮或链接触发下载时用它，因为单线程 agent 无法并发跑 `download wait` 和 `click`。点击失败会立即报错，而不是挂起等下载超时；遇到被遮挡的触发元素可加 `--force` 跳过 pointer check。

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

预设会把拦截到的调用打到 `cloak console`。`script list` 会报告每个脚本是否已注入当前页面；新注册的脚本在下次导航时先于页面脚本运行。

### 网络路由拦截

按 URL 模式拦截请求。规则跨导航持续，并在新标签页上重放。

```bash
cloak route add "**/api/*" --action abort
cloak route add "**/track" --action fulfill --status 204 --content-type application/json --body "{}"
cloak route add "*" --action continue --resource-type xhr --method POST
cloak route remove "**/api/*"         # 省略 pattern 则清空全部规则
cloak route list
```

`route list` 显示规则 id、命中次数和挂起请求 id。零命中会给出警告，避免将“登记成功”当作“拦截已验证”。不含 `*` 的模式按 URL 子串匹配，`*` 可以跨 `/` 匹配。

```bash
cloak route add --hold "/api/orders"
# 触发请求后取证 loading 状态：
cloak snapshot
cloak screenshot --output loading.png
cloak route list
cloak route release RULE_OR_REQUEST_ID
```

放行会恢复已挂起的请求，规则仍保留以处理后续请求；完成后可移除规则。挂起期间仍可快照和截图。console 消息跨导航累积，包含时间和页面 URL；`cloak console clear` 显式清空缓冲，兼容 `console show --clear`。

### 原生 CDP

```bash
cloak cdp send Runtime.evaluate --params '{"expression":"document.title","returnByValue":true}' --timeout 1000
```

命令作用于当前 session 的页面，超时按每次请求计，单位毫秒。协议错误与超时均非零退出，JSON 模式返回结构化错误。本地 raw CDP 使用独立的 per-tab 持久通道；超时或取消会重置该通道，之后需重新设置其 CDP 状态，manager 订阅保持有效。

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
cloak debugger search --url "main.js" "query"   # 按 URL 子串匹配脚本（无需 id；导航后仍有效）
cloak debugger skip-pauses true        # 忽略所有断点 / debugger;（反反调试）
```

传 `SCRIPT_ID`（来自 `debugger scripts`）或 `--url`（URL 子串）二选一。脚本 id 会在导航后失效，因此 `--url` 是按文件名搜索 bundle 的稳定方式——它会搜索所有匹配的脚本并按 URL 分组返回命中。

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

`capture export` 把裸 HAR/JSON 写到 stdout——pipe 到文件。文本模式下，`spell run` 直接打印返回值；`--json` 使用标准 envelope。PUBLIC spell 在本地运行且不启动 daemon；COOKIE、HEADER、INTERCEPT 和 UI spell 经 `/spell/run` 使用调用者当前的 Agentcloak 会话。

## Profile 管理

```bash
cloak profile create NAME [--from-current]
cloak profile list
cloak profile launch NAME
cloak profile delete NAME
```

`--from-current` 从活动浏览器抓种子数据：cookies 落到 `cookies-snapshot.json`，当前 origin 的 localStorage 落到 `localStorage-snapshot.json`——两份文件与活动 session 自己维护的快照同名同构，下次 `cloak profile launch NAME` 首次导航到对应 origin 时会自动恢复。

Profile 目录还可以放一份 `config.toml` overlay，为该 profile 单独覆盖 `[browser]` / `[security]` 配置——详见[配置参考](config.md#profile-级-config-overlay)。

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
cloak cookies restore                             # 恢复当前 profile 快照
cloak cookies restore --file /tmp/cookies.json    # 恢复指定快照
```

`cookies export` 输出 `domain | name=value` 行（每个 cookie 一行）——加上 domain
列让 agent grep 时能分辨每个 cookie 属于哪个站点。建议用 `--url` 把导出范围限定
到单个 domain；不加过滤会把当前浏览器里**所有**站点的会话一并吐出来，包括
agent 任务无关的个人账号。不带 `--output` 时，export 还会刷新
`<profile>/cookies-snapshot.json`；无活动 profile 时写入
`~/.agentcloak/cookies-snapshot.json`。`restore` 导入该文件，再运行
`cloak press Control+r` 刷新页面，让页面使用恢复后的登录态。

`cookies import` 和 `restore` 会归一化 Chrome cookies API、CDP 与 Playwright
cookie 结构，丢弃未知字段，转换 `sameSite` 和过期时间。坏条目会跳过并报告数量，
不会中止整批导入。

## 页面隐藏

```bash
cloak hide add ".feedback-toolbar"       # 有 profile 时持久保存，否则仅当前 session
cloak hide list                           # 稳定 id + 选择器 + [source]
cloak hide remove ID_OR_EXACT_SELECTOR
```

`hide list` 输出会为每条选择器打上来源标签——`[builtin]` 是不可删除的
`[data-cloak-hide]` 内置规则，`[profile]` 来自当前 profile 的 `hide.json`
持久化，`[session]` 是当前会话临时的规则（含一次性 `--hide`）：

```text
$ cloak hide list
scope: work
data-cloak-hide: [data-cloak-hide] [builtin]
feedback-toolbar: .feedback-toolbar [profile]
h1234abcd: .promo-modal [session]
```

持久选择器、一次性 `--hide` 选择器和页面声明的 `[data-cloak-hide]` 属性都会让
匹配元素退出 snapshot、截图和点击命中测试。`--keep-overlays` 可在一次 snapshot
或截图中显示全部三层。Profile 选择器保存在 `hide.json`；内置的
`[data-cloak-hide]` 规则无法删除。

## Launch

不重启 daemon 的前提下热切换 daemon 当前的浏览器 tier（以及可选的 profile）。

```bash
cloak launch --tier cloak                 # 只热切 tier，保留当前 profile
cloak launch --tier playwright --profile work   # 切 tier 并加载 profile
cloak launch --tier cloak --no-profile    # 显式清空当前 profile
```

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `--tier` / `-t` | `auto` | 后端：`auto`（→ `cloak`）、`cloak`、`playwright`、`remote_bridge` |
| `--profile` / `-p` | 保留当前 | 加载命名 profile（仅本地 tier 生效，`remote_bridge` 忽略此项） |
| `--no-profile` | 关闭 | 显式切换到无 profile，丢弃当前 profile |

省略 `--profile` 会保留 daemon 当前挂载的 profile——单独执行 `cloak launch --tier cloak` 不会再静默丢掉它。传 `--no-profile` 可解除命名 profile；shared 模式此时使用临时存储，workspace 模式仍按工作空间身份持久化；`--profile` 和 `--no-profile` 互斥，同时传会报用法错误。

## Daemon 管理

```bash
cloak daemon start [--host HOST] [--port PORT] [--headed] [--profile NAME] [--log-level info]
cloak daemon stop
cloak daemon status                # tier | browser status | seq（含 metrics 行）
```

`daemon status`（以及 MCP `agentcloak_status`）会额外打印一行 daemon 存活指标——`uptime <时长> | <N> requests | <N> active`——可当作轻量监控读数。daemon 版本过旧、不带 metrics 字段时该行省略。

`hide` 只隐藏已配置选择器命中的 DOM 元素，不会自动清理第三方 CDP 工具注入的任意标注层；需要时显式添加选择器。

## Session 管理

一个 daemon 承载多个工作空间中的 session。每个 session 独立拥有 tab、元素引用、视口、路由、脚本与 console 缓冲。同 session 请求排队；请求暂停期间仍可截图、snapshot 与放行。同名 session 在不同工作空间也互不混用页面。

`--session ID` 优先于 `AGENTCLOAK_SESSION`，默认使用规范化 worktree 或根目录的路径哈希。`--workspace PATH` 优先于 `AGENTCLOAK_WORKSPACE`、`browser.workspace_roots`、Git 仓库身份，最后回落到无 Git 的当前目录。配置根目录可覆盖子目录。MCP 在启动时识别工作空间，保留进程级 session。存储模式、持久化及恢复边界见[工作空间配置](config.md#工作空间隔离)。

```bash
cloak navigate http://localhost:5173 --session panel-a
cloak screenshot --session panel-a
cloak session list                     # id | 状态 | tier | 闲置时间
cloak session close                    # 只关闭当前调用方的 session
cloak session close panel-a            # 显式关闭指定 session
```

session 闲置 `daemon.session_idle_timeout` 秒（默认 300s）后仅回收自己的 tab，下次请求重新创建。页面关闭或本地浏览器断开后，下次请求会重建；浏览器整体故障会丢失临时页面状态，必要时需重新导航。其他 session 活跃时，切换共享 tier/profile 会报错，避免改变其他调用方的浏览器。RemoteBridge 不会把多个调用方静默映射到同一个用户 tab。

客户端探测记录中的 `/health` 来发现 daemon，只读取 `daemon.json`；PID 不可见或状态目录只读不会让活着的 daemon 被判死。原始 HTTP 可发送 `X-Agentcloak-Workspace` 和 `X-Agentcloak-Session`；缺省时使用兼容的空工作空间和 `default` session。CLI/MCP 发送解析后的身份。

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

## 恢复与证据

[有界队列、强制关闭、截图身份、URL 断言与页面 CDP 地址](../guides/recovery.md)。`session list --all` 展示标签、工作空间路径、进行中动作与排队数；`tab close --others` 只影响当前会话。

`tab close --others` 的 `closed` 为实际关闭的 ID 列表；文本输出显示数量与 ID，没有其他标签页时显示 `closed 0 tabs`。

## batch

```bash
cloak batch --calls-file calls.jsonl --json
# Or pipe JSONL into: cloak batch --json
```

```jsonl
{"method":"POST","path":"/navigate","body":{"url":"https://example.com"}}
{"method":"POST","path":"/action","body":{"kind":"click","selector":"#save"}}
{"method":"GET","path":"/snapshot","params":{"find":"Saved"}}
```

每行是一个 daemon JSON 请求：`method`、相对 `path`，以及可选的 `params`、`body`。整个序列复用一个进程和 HTTP 连接池；会话/工作空间沿用全局参数与配置，不能逐行切换。不接受完整 URL 或任意 headers；查询字段放入 `params`。

`--json` 每条输出一个紧凑信封，增加从零开始的 `index` 和从一开始的输入 `line`，读取下一行前立即刷新输出（`--pretty` 不展开 JSONL）。遇到第一条非法输入或请求失败，输出 `ok:false` 并非零退出；已经执行的操作保留，不回滚、不自动重放动作。空输入不执行操作。请求字段见生成的 HTTP 路由参考。原有 `cloak do batch --calls-file` 保留动作批处理、结果引用和导航/对话框中断规则。

## record

```bash
cloak record start --format webm --max-seconds 120 --max-frames 600
cloak record status
cloak record stop -o transition.webm
# No encoder required:
cloak record start --format zip
cloak record stop -o frames.zip
cloak screenshot --annotate --dpr 2 -o annotated.png --json
```

录屏使用独立 CDP screencast，固定录制启动时的活动标签页。该页导航继续录制，切换标签不会切换录制目标；每个 session 独立持有录屏。不录制音频。支持本地 Playwright/CloakBrowser，RemoteBridge 明确拒绝。

WebM 导出要求 daemon 所在机器安装含 VP9 编码器的 `ffmpeg`。ZIP 包含 JPEG 帧和 `manifest.json`，记录逐帧 URL、经过时间和 CDP 元数据。screencast 记录合成器更新，并非固定 FPS；WebM 保留时间间隔。帧尺寸上限 1920×1080，视口变化时按首帧尺寸等比缩放并补边。默认最多 600 帧/120 秒，可配置到 3000 帧/600 秒，帧数据另有固定 64 MiB 上限。达到限制或录制页关闭后停止采集、保留帧直到 `record stop`；关闭 session 则丢弃未导出的录屏。未导出的录屏会阻止再次 start。stop 将文件写到 CLI/MCP 客户端机器，HTTP 返回 base64。

`--annotate` 生成新的 snapshot 引用，在图片上绘制元素框及 `[N]`，不向页面添加覆盖层。JSON 增加 `annotated` 和 `annotations`（`ref`、`role`、`name`、`box`）。box 单位为 CSS 像素，视口截图相对视口，全页截图相对文档，绘图按实际 DPR 缩放。几何信息来自原生 CDP，避免 JavaScript 指纹扰动。已移除或未渲染的节点没有框。返回引用对应此次截图的页面状态，之后 DOM 变化需重新 snapshot；临时视口和 DPR 仍会恢复。
