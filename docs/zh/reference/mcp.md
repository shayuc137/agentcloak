# MCP 工具参考

agentcloak 的 MCP server 通过 stdio 传输暴露 36 个工具。已包含在基础安装中（`pip install agentcloak`），运行命令：`agentcloak-mcp`。

配置说明参见 [MCP 配置指南](../guides/mcp-setup.md)。

## 响应格式

工具返回的文本与 CLI 打印的内容一致：daemon 只输出 JSON 信封，CLI 和 MCP 共用
`core/text_renderers` 在本地渲染，所以同一份 daemon payload 在 MCP 这边输出的
文本与 `cloak <command>` 字节级相同。错误仍然走三字段 JSON 信封
（`{"error", "hint", "action"}`）—— 这是 MCP 客户端解析失败时已经使用的 schema，
和 CLI `--json` 契约保持一致。

`agentcloak_screenshot` 是唯一例外：返回 MCP `ImageContent`，让多模态 LLM 直接读
像素，省掉 base64 来回转换的开销；再附带一条短 `TextContent` 携带 size/format 元
数据。

## 导航

### agentcloak_navigate

导航浏览器到指定 URL。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `url` | `str` | 必填 | 目标 URL（http:// 或 https://） |
| `timeout` | `float` | `30.0` | 等待页面加载的最大秒数 |
| `include_snapshot` | `bool` | `false` | 在响应中包含无障碍树 snapshot |
| `snapshot_mode` | `str` | `compact` | `include_snapshot` 为 true 时的 snapshot 模式 |

### agentcloak_snapshot

获取带有 `[N]` 元素引用的无障碍树。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `mode` | `str` | `compact` | `compact`（默认）、`accessible`、`content` 或 `dom` |
| `max_chars` | `int` | `0` | 截断 tree_text 到 N 个字符（0 = 不限制） |
| `max_nodes` | `int` | `0` | 在 N 个节点后截断（0 = 不限制） |
| `focus` | `int` | `0` | 展开元素 `[N]` 周围的子树 |
| `offset` | `int` | `0` | 从第 N 个元素开始（分页） |
| `frames` | `bool` | `false` | 包含 iframe 内容 |
| `diff` | `bool` | `false` | 标记与上一次 snapshot 相比的变更 |

### agentcloak_screenshot

截取当前页面的屏幕截图。返回 `ImageContent`（图像字节）加一条带 size/format 元数据的 `TextContent` —— 多模态 LLM 直接读像素，agent 端无需 base64 来回转换。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `full_page` | `bool` | `false` | 捕获完整可滚动页面 |
| `format` | `str` | `jpeg` | `jpeg` 或 `png` |
| `quality` | `int` | `config.mcp_screenshot_quality` | JPEG 质量 0-100（默认比 CLI 小，匹配 MCP token 预算） |

## 交互

### agentcloak_action

使用 `[N]` 元素引用与页面交互。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `kind` | `str` | 必填 | `click`、`fill`、`type`、`scroll`、`hover`、`select`、`press`、`keydown`、`keyup` |
| `target` | `str` | `""` | 元素 `[N]` 引用（scroll/press/key 时可为空） |
| `text` | `str` | `""` | fill/type 的文本 |
| `key` | `str` | `""` | press/keydown/keyup 的按键（如 `Enter`、`Control+a`） |
| `value` | `str` | `""` | select 的选项值 |
| `direction` | `str` | `down` | 滚动方向（up/down） |
| `include_snapshot` | `bool` | `false` | 在响应中附带 compact snapshot |

返回值包含主动状态反馈：`pending_requests`、`dialog`、`navigation`、`current_value`。

## 内容

### agentcloak_evaluate

在浏览器页面上下文中执行 JavaScript。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `js` | `str` | 必填 | 要执行的 JavaScript 代码 |
| `world` | `str` | `main` | `main`（可见页面全局对象）或 `utility`（隔离环境） |
| `max_return_size` | `int` | `50000` | 序列化结果的最大字节数 |

### agentcloak_fetch

使用浏览器的 cookie 和 user agent 发起 HTTP 请求。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `url` | `str` | 必填 | 请求 URL |
| `method` | `str` | `GET` | HTTP 方法 |
| `body` | `str` | `null` | POST/PUT 的请求体 |
| `headers_json` | `str` | `null` | 额外 header（JSON 对象） |
| `timeout` | `float` | `30.0` | 超时秒数 |

## 网络

### agentcloak_network

列出捕获的网络请求。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `since` | `str` | `0` | seq 编号或 `last_action` |

## 捕获

### agentcloak_capture_control

控制网络流量录制。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | 必填 | `start`、`stop`、`clear` 或 `replay` |
| `url` | `str` | `""` | replay 操作的 URL |
| `method` | `str` | `GET` | replay 的 HTTP 方法 |

### agentcloak_capture_query

查询捕获的流量数据。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | `status` | `status`、`export` 或 `analyze` |
| `format` | `str` | `har` | 导出格式：`har` 或 `json` |
| `domain` | `str` | `""` | 按域名过滤（用于 analyze） |

## 对话框

### agentcloak_dialog

处理浏览器对话框（alert、confirm、prompt）。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `kind` | `str` | `status` | `status`、`accept` 或 `dismiss` |
| `text` | `str` | `""` | prompt 对话框的回复文本（配合 accept 使用） |

## 等待

### agentcloak_wait

等待满足指定条件后继续。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `condition` | `str` | 必填 | `selector`、`url`、`load`、`js` 或 `ms` |
| `value` | `str` | `""` | 选择器/URL/状态/表达式/毫秒数 |
| `timeout` | `int` | `30000` | 最大等待时间（毫秒） |
| `state` | `str` | `visible` | selector 的元素状态：`visible`、`hidden`、`attached`、`detached` |

## 上传

### agentcloak_upload

向文件输入元素上传文件。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `index` | `int` | 必填 | 文件输入的元素 `[N]` 引用 |
| `files` | `list[str]` | 必填 | 绝对文件路径列表 |

## Frame

### agentcloak_frame

列出或切换页面 frame。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `kind` | `str` | `list` | `list` 或 `focus` |
| `name` | `str` | `""` | 要切换到的 frame 名称 |
| `url` | `str` | `""` | 匹配的 URL 子串 |
| `main` | `bool` | `false` | 切换到主 frame |

## 管理

### agentcloak_status

查询 daemon 和浏览器状态。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `query` | `str` | `health` | `health` 或 `cdp_endpoint` |

### agentcloak_launch

启动或重启浏览器 daemon。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `tier` | `str` | `""` | `auto`、`cloak`、`playwright` 或 `remote_bridge` |
| `profile` | `str` | `""` | 命名的浏览器 profile |

### agentcloak_tab

管理浏览器标签页。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | `list` | `list`、`new`、`close` 或 `switch` |
| `tab_id` | `int` | `-1` | 标签页 ID（用于 close/switch） |
| `url` | `str` | `""` | 新标签页的 URL |

### agentcloak_profile

管理浏览器 profile。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | `list` | `create`、`list` 或 `delete` |
| `name` | `str` | `""` | Profile 名称 |
| `from_current` | `bool` | `false` | 从当前会话复制 cookie（仅 create） |

### agentcloak_doctor

运行安装诊断检查。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `fix` | `bool` | `false` | 尝试进程内修复（下载 binary、创建数据目录） |
| `detail` | `bool` | `false` | 显示每项检查（详细模式）。默认返回精简 2 行摘要 + 运行状态 |

默认输出：通过数 + 版本 + 浏览器描述、headless/headed、humanize、proxy、profile。

### agentcloak_resume

获取会话恢复快照用于上下文恢复。

无参数。返回当前 URL、打开的标签页、最近 5 次操作、捕获状态、隐身等级和时间戳。

## Cookie

### agentcloak_cookies

管理浏览器 cookie。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | `export` | `export` 或 `import` |
| `url` | `str` | `""` | 按 URL 过滤（仅 export） |
| `cookies_json` | `str` | `""` | cookie 对象的 JSON 数组（仅 import） |

## Spell

### agentcloak_spell_run

按名称运行已注册的 spell。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `name` | `str` | 必填 | Spell 名称，格式为 `site/command` |
| `args_json` | `str` | `{}` | 参数（JSON 对象） |

### agentcloak_spell_list

列出所有已注册的 spell。

无参数。返回包含 site、name、strategy 和 description 的 spell 数组。

## Bridge

### agentcloak_bridge

通过 Chrome 扩展 bridge 管理远程浏览器标签页。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | `claim` | `claim` 或 `finalize` |
| `tab_id` | `int` | `-1` | Chrome 标签页 ID（仅 claim） |
| `url_pattern` | `str` | `""` | URL 子串匹配（仅 claim） |
| `mode` | `str` | `close` | finalize 模式：`close`、`handoff` 或 `deliverable` |

## 控制台、下载与存储

### agentcloak_console

读取浏览器控制台输出或清空缓冲区（console.log/warn/error 及未捕获异常）。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `action` | `str` | `show` | `show` 读取消息，`clear` 清空缓冲区 |
| `since` | `int` | `0` | 只返回 seq > since 的条目（分页） |
| `limit` | `int` | `0` | 最多返回条数（0 = 全部） |
| `level` | `str` | `""` | 按级别过滤：`log`、`warn`、`error`、`info`、`debug` |

### agentcloak_download

下载文件——直接获取 URL 或捕获点击触发的下载。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `action` | `str` | `url` | `url`（直接下载）、`wait`（等点击触发下载）、`list`（列出已下载） |
| `url` | `str` | `""` | 目标 URL（仅 action=url，受 SSRF 检查） |
| `output_dir` | `str` | `""` | 保存目录（daemon 主机路径） |
| `timeout` | `float` | `0.0` | 等待点击触发下载的最大秒数 |

### agentcloak_storage

读写页面的 localStorage / sessionStorage。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `action` | `str` | `get` | `get`、`set`、`delete` 或 `clear` |
| `type` | `str` | `local` | `local`（localStorage）或 `session`（sessionStorage） |
| `key` | `str` | `""` | 要读写/删除的键（省略则 get 全部或 clear） |
| `value` | `str` | `""` | 要写入的值（仅 action=set） |

### agentcloak_clipboard

读写系统剪贴板。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `action` | `str` | `read` | `read` 或 `write` |
| `text` | `str` | `""` | 要写入的文本（仅 action=write） |

注意：clipboard-read 需要 headed 浏览器或 RemoteBridge（Chromium headless 模式下阻止读取）。

### agentcloak_pdf

将当前页面导出为 PDF 文件（仅 headless Chromium 支持）。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `output_path` | `str` | 必填 | daemon 主机上的保存路径 |
| `format` | `str` | `A4` | 纸张格式（A4、Letter、Legal 等） |
| `landscape` | `bool` | `false` | 横向排版 |
| `scale` | `float` | `0.0` | 缩放比例（0 = 浏览器默认） |
| `page_ranges` | `str` | `""` | 如 `"1-3, 5"` |

### agentcloak_serve

将本地目录通过 HTTP 提供访问，让你能导航到本地文件（`file://` 被安全层拦截）。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `action` | `str` | `status` | `start`、`stop` 或 `status` |
| `directory` | `str` | `""` | 要服务的目录（仅 action=start） |
| `port` | `int` | `0` | 端口号（0 = 自动分配） |

## 网页逆向

基于 CDP 的工具，用于检视和操纵页面内部。每个 manager 在首次使用时才惰性 enable 对应的 CDP 域——从不做逆向的会话零开销——且三种后端（CloakBrowser、Playwright、RemoteBridge）全部通用。

### agentcloak_script

注入在每次导航时先于页面脚本运行的 JavaScript——这是在页面用到 `fetch` / `XHR` / `JSON.parse` 之前打补丁的标准 hook 点（不同于 `agentcloak_evaluate`，后者在页面加载后才执行）。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | `list` | `add`、`remove` 或 `list` |
| `js` | `str` | `""` | 要注入的原始 JavaScript（用于 `add`） |
| `preset` | `str` | `""` | 内置 hook 预设（用于 `add`，覆盖 `js`）：`fetch`、`xhr`、`json_parse`、`crypto`、`timing` |
| `identifier` | `str` | `""` | 要移除的脚本标识符（用于 `remove`） |

预设会把拦截到的调用打到 console（用 `agentcloak_console` 读取）。

### agentcloak_route

按 URL 模式拦截网络请求（abort / fulfill / continue）。规则跨导航持续，并在新标签页上重放。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | `list` | `add`、`remove` 或 `list` |
| `pattern` | `str` | `""` | URL glob（`*` = 任意字符；不含 `*` = 子串匹配） |
| `rule_action` | `str` | `continue` | `add` 的处置方式：`abort`、`fulfill` 或 `continue` |
| `resource_type` | `str` | `""` | 只匹配该资源类型（`xhr`、`image`...） |
| `method` | `str` | `""` | 只匹配该 HTTP 方法 |
| `status` | `int` | `0` | `fulfill` 规则的响应状态码（默认 200） |
| `content_type` | `str` | `""` | `fulfill` 响应的 Content-Type |
| `body` | `str` | `""` | `fulfill` 响应的 body |

### agentcloak_headers

设置应用到后续每个请求的额外 HTTP header——调试 API 时伪造 Authorization token 或自定义 header。不传 header 则清空覆盖。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `headers` | `dict[str, str]` | `null` | header 名 → 值映射。空/null 清空所有覆盖 |

### agentcloak_graphql

通过浏览器会话（继承页面 cookie，通过安全域名检查）introspect GraphQL schema 或发送任意查询。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | `introspect` | `introspect` 或 `query` |
| `url` | `str` | `""` | GraphQL 端点 URL |
| `query` | `str` | `""` | GraphQL 文档（用于 `query`） |
| `variables` | `dict` | `null` | GraphQL 变量对象（用于 `query`） |
| `headers` | `dict` | `null` | 额外请求 header（如 auth token） |

### agentcloak_streaming

捕获 WebSocket 帧和 Server-Sent Events——普通 network 视图看不到的流量。帧和事件落入按单调 seq 分页的 ring buffer，与 console 一致。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | `ws_messages` | `ws_list`、`ws_messages` 或 `sse_messages` |
| `since` | `int` | `0` | 只返回 seq 大于该值的帧/事件 |

### agentcloak_debugger

通过 CDP Debugger 域检视和控制暂停的 JavaScript 执行：设断点、单步、读调用栈和作用域。该域在首次 `enable` / `breakpoint_set` 时惰性开启。暂停期间，页面操作（navigate、click...）返回 `debugger_paused` 错误——调用 `resume` 或 `step`。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | `paused_info` | `enable`、`disable`、`breakpoint_set`、`breakpoint_remove`、`breakpoint_list`、`xhr_set`、`xhr_remove`、`resume`、`step`、`paused_info`、`scope_variables`、`evaluate`、`scripts`、`script_source`、`search`、`skip_pauses` |
| `url` | `str` | `""` | 脚本 URL 正则（用于 `breakpoint_set`） |
| `line` | `int` | `0` | 从 0 开始的行号（用于 `breakpoint_set`） |
| `condition` | `str` | `""` | 仅当此 JS 表达式为真时断下 |
| `breakpoint_id` | `str` | `""` | 断点 id（用于 `breakpoint_remove`） |
| `url_pattern` | `str` | `""` | XHR URL 子串（用于 `xhr_set` / `xhr_remove`；空 = 所有 XHR） |
| `step_type` | `str` | `over` | `over`、`into` 或 `out`（用于 `step`） |
| `object_id` | `str` | `""` | 来自某帧 scopeChain 的作用域 objectId（用于 `scope_variables`） |
| `call_frame_id` | `str` | `""` | callFrameId（用于 `evaluate`） |
| `expression` | `str` | `""` | 在暂停帧中求值的表达式 |
| `script_id` | `str` | `""` | 脚本 id（用于 `script_source` / `search`） |
| `query` | `str` | `""` | 子串或正则（用于 `search`） |
| `is_regex` | `bool` | `false` | 将 `query` 当作正则 |
| `case_sensitive` | `bool` | `false` | 区分大小写搜索 |
| `skip` | `bool` | `true` | 用于 `skip_pauses`：忽略所有断点 / `debugger;`（反反调试） |

### agentcloak_sourcemap

发现并解析 source map（纯 Python VLQ 解码），将编译后的 `line:column` 反查回原始源文件并读取其文本。构建在 debugger 的脚本清单之上。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `action` | `str` | `list` | `list`、`get`、`lookup`、`sources` 或 `source_content` |
| `script_id` | `str` | `""` | 来自 `list` 的 CDP 脚本 id |
| `line` | `int` | `0` | 从 0 开始的生成（编译）行（用于 `lookup`） |
| `column` | `int` | `0` | 从 0 开始的生成列（用于 `lookup`） |
| `source_path` | `str` | `""` | 来自 `sources` 的路径（用于 `source_content`） |
