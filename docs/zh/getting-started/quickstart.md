# 快速开始

本教程介绍 agentcloak 的核心工作流：导航到页面、通过无障碍树 snapshot 读取页面、与元素交互、验证结果。

## 观察-操作循环

agentcloak 基于一个简单的循环：

1. **导航**到页面
2. **Snapshot** 查看带有 `[N]` 元素引用的无障碍树
3. **操作**元素，使用其 `[N]` 引用编号
4. **重新 snapshot**（页面更新后引用编号会变化）

## 首次运行

daemon 在首次命令时自动启动，无需手动设置。

```bash
# 导航并一步获取 snapshot
cloak navigate "https://example.com" --snap
```

stdout 直接就是答案——一行导航结果，接着是 snapshot 树：

```text
https://example.com/ | Example Domain

# Example Domain | https://example.com/ | 8 nodes (1 interactive) | seq=1
  heading "Example Domain" level=1
  paragraph "This domain is for use in illustrative examples in documents."
  [1] link "Learn more" href="https://iana.org/domains/example"
```

## 阅读 snapshot

snapshot 是一棵无障碍树，每个可交互元素分配一个 `[N]` 引用：

```text
# Example Domain | https://example.com/ | 8 nodes (1 interactive) | seq=1
  heading "Example Domain" level=1
  paragraph "This domain is for use in illustrative examples in documents."
  [1] link "Learn more" href="https://iana.org/domains/example"
```

- 头行包含页面标题、URL、节点数量、daemon `seq`（状态计数器）
- 没有 `[N]` 前缀的行是容器/上下文元素
- 带 `[N]` 的行是可交互元素——把数字传给 click/fill 等命令
- 输入框显示当前值；密码字段会脱敏为 `••••`

### Snapshot 模式

| 模式 | 显示内容 | 使用场景 |
|------|---------|---------|
| `compact` | 仅可交互元素和命名容器 | 默认——token 效率高 |
| `accessible` | 完整无障碍树，带 `[N]` 引用、ARIA 状态、值 | 需要看完整层级时 |
| `content` | 文本提取 | 阅读文章或文本密集页面 |
| `dom` | 原始 HTML | 调试或 CSS 选择器相关工作 |

```bash
cloak snapshot --mode accessible     # 完整无障碍树
cloak snapshot --mode content        # 文本提取
```

## 与元素交互

使用 snapshot 中的 `[N]` 引用作为操作目标。元素索引支持位置参数（agent 更简短）或 `--index N`：

```bash
# 点击链接（位置参数或 --index 都行）
cloak click 1

# 填充文本字段
cloak fill 5 "search query"

# 按键
cloak press Enter

# 选择下拉选项
cloak select 8 --value "option-2"
```

每个 action 打印一行确认 + 主动反馈：

```text
$ cloak click 1
clicked [1]
  navigation: https://www.iana.org/domains/example
```

### 获取操作后状态

给任何 action 命令添加 `--snap`，可在同一响应中获取 snapshot——比单独再跑 `cloak snapshot` 省一次往返：

```bash
cloak click 2 --snap
```

```text
clicked [2]
  navigation: https://example.com/page2

# Page Two | https://example.com/page2 | 12 nodes (4 interactive) | seq=4
  ...
```

## 完整登录示例

```bash
# 导航到登录页并获取 snapshot
cloak navigate "https://example.com/login" --snap
# Snapshot 输出（节选）：
# heading "Sign In" level=1
# [1] textbox "Email"
# [2] textbox "Password" value="••••"
# [3] button "Sign In"

# 填写凭据
cloak fill 1 "user@example.com"
cloak fill 2 "my-password"

# 提交并获取新 snapshot
cloak click 3 --snap

# 保存登录状态以便复用
cloak profile create my-session
```

下次使用保存的 profile 启动：

```bash
cloak daemon start --profile my-session
cloak navigate "https://example.com/dashboard" --snap
```

## 网络监控

查看页面发出的网络请求：

```bash
# 查看最近的请求
cloak network requests

# 仅查看上一次操作以来的请求
cloak network requests --since last_action
```

## 截图

```bash
# 视口截图（JPEG，比 PNG 小约 75-85%）
cloak screenshot
# stdout 输出位于系统临时目录下的文件路径，例如：
#   Linux/macOS: /tmp/agentcloak-1715920000.jpg
#   Windows:     C:\Users\you\AppData\Local\Temp\agentcloak-1715920000.jpg

# 完整可滚动页面
cloak screenshot --full-page

# PNG 格式获取像素级精确度
cloak screenshot --format png

# 保存到指定文件
cloak screenshot --output page.png
```

## 处理大页面

对于元素众多的页面，使用渐进加载：

```bash
# 限制输出为 80 个节点（--max-nodes 仍兼容）
cloak snapshot --limit 80

# 分页浏览结果
cloak snapshot --offset 80 --limit 80

# 聚焦特定元素的子树
cloak snapshot --focus 15

# 查看操作后的变更
cloak snapshot --diff
```

## 捕获 API 流量

录制和分析网络流量以发现 API 模式：

```bash
cloak capture start
cloak navigate "https://api-heavy-site.com"
# 与页面交互...
cloak capture stop

# 导出为 HAR（裸字节到 stdout，pipe 到文件）
cloak capture export --format har > traffic.har

# 自动检测 API 模式
cloak capture analyze
```

详情参见[流量捕获指南](../guides/capture.md)。

## 输出格式

CLI 以文本为先 **stdout 就是答案**。提示和错误走 stderr；exit code 为 0 成功 / 1 失败 / 2 用法错误。

```text
$ cloak navigate https://example.com
https://example.com/ | Example Domain

$ cloak click 99
Error: Element [99] not in selector_map (1 entries)
  -> run 'snapshot' to refresh the selector_map, or re-snapshot if the page changed
```

需要旧的 JSON envelope（`jq` 流水线或其他结构化消费方）？加 `--json`，或设 `AGENTCLOAK_OUTPUT=json`：

```bash
cloak --json snapshot | jq -r '.data.tree_text'
AGENTCLOAK_OUTPUT=json cloak snapshot
```

`--json` 生效时的 JSON shape：

```json
{"ok": true, "seq": 3, "data": {"url": "https://example.com", "title": "Example"}}
{"ok": false, "error": "element_not_found", "hint": "No element at index 99", "action": "re-snapshot to get fresh [N] refs"}
```

`seq` 是单调递增计数器，每次浏览器状态变化时递增。

## 后续步骤

- [CLI 参考](../reference/cli.md) -- 所有命令和参数
- [浏览器后端](../guides/backends.md) -- CloakBrowser 与 Playwright 与 RemoteBridge 对比
- [MCP 配置](../guides/mcp-setup.md) -- 通过 MCP 从 AI 客户端连接
- [配置参考](../reference/config.md) -- 自定义 daemon 行为
