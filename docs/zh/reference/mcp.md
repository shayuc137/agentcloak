# MCP 工具参考

agentcloak 的 MCP server 通过 stdio 传输暴露 23 个工具。已包含在基础安装中（`pip install agentcloak`），运行命令：`agentcloak-mcp`。

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
