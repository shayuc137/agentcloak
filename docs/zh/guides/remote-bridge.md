# Remote Bridge

Remote Bridge 让 agentcloak daemon 驱动运行在另一台机器上的真实 Chrome 浏览器——例如 Linux 服务器上的 agent 操控你 Windows 桌面的 Chrome，带上所有登录态、扩展、以及数月正常使用积累出的真实指纹。没有 headless 检测，无需 cookie 来回搬运。

## 架构

```
┌──────────────────┐   HTTP    ┌──────────────────┐    WS     ┌─────────────────────┐
│  cloak CLI / MCP │ ────────► │  daemon (Linux)  │ ◄──────►  │  Chrome 扩展          │
│                  │           │  18765 + /ext WS │           │  (Windows / macOS)  │
└──────────────────┘           └──────────────────┘           └─────────────────────┘
```

Chrome 扩展通过 `chrome.debugger` 说 CDP，把 daemon 的每条命令通过 WebSocket 隧道转发。daemon 的 `RemoteBridgeAdapter` 把 Playwright 风格的请求翻译成原始 CDP 命令并通过隧道送出。

扩展直接连 daemon 的 `/ext` WebSocket——把它指向 daemon 的 host:port（默认自动探测 `18765-18774` 范围）。只要 daemon 监听在扩展所在机器能访问的网卡上即可，不需要单独的中继进程。

## 配置步骤

### 1. 安装扩展

在 daemon 所在机器：

```bash
cloak bridge extension-path
# /home/you/.local/lib/python3.13/site-packages/agentcloak/bridge/agentcloak-chrome-extension
```

把这个目录复制到 Chrome 所在的机器，然后在 Chrome 里：

1. 打开 `chrome://extensions`
2. 启用**开发者模式**（右上角开关）
3. 点击**加载已解压的扩展**，选中扩展目录
4. 工具栏会出现扩展图标，带红色"未连接"角标

### 2. 连接扩展

点击扩展图标，填入 daemon 地址。扩展会在配置的主机上探测 `18765-18774` 端口，自动连上第一个响应 `/ext` 的 daemon。连接成功后角标转绿。

家庭网络下最简单的配置：

- Linux daemon：`cloak daemon start -b --host 0.0.0.0`（绑定所有接口让 LAN 能访问）
- 扩展选项：host = Linux 服务器 IP，port = 18765

### 3. 使用 bridge

扩展变绿后，所有常规命令照常工作——只是驱动的是真实浏览器：

```bash
cloak launch --tier remote_bridge
cloak navigate "https://example.com"
cloak snapshot                                   # 看到真实页面
cloak click 5                                    # 在真实 Chrome 里点击
```

让 bridge 成为 daemon 默认后端：

```bash
export AGENTCLOAK_DEFAULT_TIER=remote_bridge
```

## 通过 bridge 做网页逆向

Phase 7b 的所有网页逆向能力都能通过 bridge 工作——调试器断点、网络路由拦截、WebSocket/SSE 流式监控、source map、init script 注入、GraphQL。扩展会在 agent 首次使用需要某个 CDP 域的能力时**按需** enable 对应的域（`Debugger`、`Fetch`、`Network`），而不是为每个会话一直开着。命令与本地后端完全一致，详见 CLI 参考的[网页逆向](../reference/cli.md#网页逆向)段。

## 隐私提示

RemoteBridge 让 agent 看到的浏览器视图和你本人完全一致。`cloak tab list`
返回**所有打开的标签页**——包括个人邮箱、网银、办公工具——而不只是
agent 显式接管的那些。URL 和标题一旦被读取就进入 agent 的上下文窗口，
等于把标题栏里的所有内容都共享给驱动这次会话的模型。

实际可用的护栏：

- 连接前关闭（或移到独立 Chrome profile）不希望 agent 看到的标签。
- 优先用 `cloak bridge claim --url-pattern ...` 把单个 tab 显式纳入控制，
  而不是让 agent 扫整张标签表。
- 留意蓝色 "agentcloak" tab group——分组之外的内容仍然可读，但分组本身
  是一种视觉提醒，告诉你 agent 的标称作用域是哪些。

## 标签页接管

bridge 启动时没有被托管的标签页。两种方式把 tab 纳入 agent 控制：

```bash
# agent 自己开新 tab
cloak tab new --url "https://github.com"

# 或接管用户已经打开的 tab
cloak bridge claim --url-pattern "github.com"     # URL 含 "github.com" 的第一个 tab
cloak bridge claim --tab-id 1234                  # 指定 Chrome tab id
```

被接管的 tab 会加入名为 **agentcloak** 的蓝色 Chrome tab group，用户能直观地把 agent 控制的 tab 与自己的区分开。

## 会话收尾

agent 完成任务后，用三种模式之一收尾：

```bash
cloak bridge finalize --mode close         # 关闭所有 agent 托管的 tab
cloak bridge finalize --mode handoff       # 解除分组，保留 tab
cloak bridge finalize --mode deliverable   # 重命名分组为 "agentcloak results"（绿色）
```

按交接意图选择：`close` 用于全自主跑完，`handoff` 用于"接下来用户手动继续"，`deliverable` 用于标记需要用户审阅的结果。

## WebSocket 认证

扩展直接连 daemon 的 `/ext` WebSocket。该端点接受 Bearer token（daemon 启动时自动生成，打印在日志里，存在 session 文件中）。扩展通过选项 UI 取到它，并在 `hello` 消息里发送——浏览器 WebSocket API 无法设置请求头，所以 token 走带内传递。

- **localhost 连接**绕过认证（你本来就在本机）
- **远程连接**必须在 `hello` 消息里带上 token；不匹配会以 `4001` 关闭

用 `cloak bridge token --reset` 轮换（对运行中的 daemon 热生效），或重启 daemon。

重启 daemon 即轮换——每次启动会生成新 token。

## mDNS 自动发现（可选）

装上可选的 `zeroconf` 依赖（`pip install agentcloak[mdns]`），daemon 会在局域网上广播自己为 `_agentcloak._tcp.local`。扩展可以列出可用 daemon，无需手输 IP。

认证 token **绝不**走 mDNS 广播——客户端仍需从 session 文件取。

## Cookie 导出

从真实浏览器拉 cookie 用于脚本或植入 profile：

```bash
cloak cookies export                   # 全部 domain，JSON 到 stdout
cloak cookies export --url github.com  # 只一个 domain
cloak cookies import < cookies.json    # 注入当前上下文
```

这是把手动登录升级为可复用 profile 的最简单路径——在真实 Chrome 登录、导出、再导入到新的 agentcloak profile。

## 故障排查

```bash
cloak bridge doctor
```

这会检查：扩展可达、WebSocket 已连、daemon `/ext` 端点活、扩展最近心跳时间戳。

| 现象 | 第一步 |
|------|-------|
| 扩展角标持续红色 | 确认 daemon 用了 `--host 0.0.0.0` 并放行防火墙端口 |
| `bridge_disconnected` 报错 | 跑 `cloak bridge doctor`；在 `chrome://extensions` 重载扩展 |
| `navigate` 命令挂起 | Chrome 可能弹了权限框堵住——聚焦 Chrome 窗口处理掉 |
| 远程 LAN token 不匹配 | 从 `~/.agentcloak/session.json` 重新读 token 粘到扩展选项 |
| 重启 Chrome 后扩展掉线 | 扩展用了 `chrome.alarms` keepalive 但 Chrome 偶尔挂起 MV3 service worker——点一下扩展图标唤醒 |
