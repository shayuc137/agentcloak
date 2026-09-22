# 会话恢复与可靠的浏览器证据

每个会话的请求队列都有上限。等锁沿用 `browser.action_timeout`（毫秒），超时返回 `session_busy` 并指出占用路由。执行也默认受 action timeout 约束，包含快照与截图。显式 navigation/fetch 超时按秒、wait/CDP 按毫秒，可以延长执行预算；清理阶段可能增加短暂且有界的延迟。

本地后端在导航取消或超时后，会先执行有界的浏览器侧停止加载清理，再释放会话队列，避免已放弃的导航稍后恢复并打断下一条命令。如果浏览器无法确认清理，请先强制关闭会话再重试。

```bash
cloak session list --all
cloak session close --force
cloak navigate https://example.com/dashboard --expect-path /dashboard
cloak screenshot --expect-url 'https://example.com/dashboard*' --viewport 1280x800 -o page.png --json
cloak cdp endpoint --page
```

`session list` 展示稳定 ID、可读目录标签、进行中动作和排队数；`--all` 同时展示其他工作空间及规范路径。标签只用于显示，哈希身份仍是命名空间键。HTTP 客户端可在既有 workspace/session 头外提供百分号编码的 `X-Agentcloak-Session-Label` 和 `X-Agentcloak-Workspace-Path`。

本地后端的 `close --force` 绕过普通调度，取消指定会话的请求，终止冻结页面的执行并关闭其标签页。它不重启 daemon，也不关闭其他会话。会话队列与标签页归属相互隔离，但共享 context 的会话可能共用网络连接池和渲染进程。因此，资源耗尽或浏览器侧阻塞可能影响其他会话；关闭问题会话可释放共享资源。工作空间存储隔离也不保证每个会话拥有独立浏览器进程。强制恢复跳过依赖渲染进程的存储采集，保留 workspace context；正常关闭仍会持久化工作空间存储。HTTP 客户端断开后，对应 daemon 请求会取消。RemoteBridge 强制关闭返回 `force_recovery_unavailable`，需使用其正常的放行、重连流程。

页面或浏览器意外消失时返回 `page_recreated` 或 `page_lost`，采集证据前必须重新导航，不能静默使用重建的空白页。`/health` 不生成 snapshot、不替换元素引用，并限制尽力读取页面信息的耗时。

截图 JSON 包含 `url`、`title`、`viewport`（CSS 宽高）、`dpr`、`pixel_width` 和 `pixel_height`（从实际图片解码）。临时视口元数据描述截图时的状态，不是恢复后的视口。输出父目录必须已存在。`--expect-url` 使用区分大小写的 glob；`navigate --expect-path` 精确检查最终 URL 的 pathname，不包含 query 与 fragment。不匹配时失败且不写截图；截图过程中发生导航返回 `page_changed`。

本地后端 `cdp endpoint --page` 返回当前会话的准确页面 target 地址及 `target_id`，不加参数仍返回浏览器级地址。页面选择不构成 CDP 权限隔离。

动作与求值返回 `new_tab`，弹窗累计较多时会提示。仍在加载的弹窗可能先返回 `pending: true` 而没有 tab ID，此时先用 `tab list` 确认。`tab close --others` 只关闭本会话的其他标签页，JSON 的 `closed` 返回实际关闭的 ID；没有其他页面时重复调用返回空列表。文本输出显示关闭数量与 ID。不再需要的弹窗应及时关闭。键名与修饰键别名大小写不敏感；无效组合在输入前拒绝，失败或取消时释放本次新按下的修饰键。`network --since last_action` 包含最近动作自身触发的请求；数字 `--since N` 仍是开区间。route 模式的 `*` 是通配符，`?` 是字面查询分隔符，`*/items?*` 与 `*/items/?*` 的斜杠有区别。

启动时使用 `daemon start --log-level info` 记录请求进入、取锁、开始、完成的时间与 session，覆盖本进程的 `daemon.log_level`。Unix 下 `kill -USR1 <daemon-pid>` 将 asyncio 任务栈写入日志，不需要 ptrace。`cloak version --json`、`/health` 和 `daemon.json` 包含 `build_id`，`--version` 也会打印。内容指纹可区分已安装 wheel 与本地修改；Git checkout 还会显示提交号。
## 长连接与导航阻塞

Chromium 的普通连接池通常允许每个目标建立六条 HTTP/1.x 连接（[上游实现](https://github.com/chromium/chromium/blob/main/net/socket/client_socket_pool_manager.cc)）。SSE 响应长期保持连接：共享 context 的页面占满连接池后，其他会话到同一目标的导航和 API 请求也可能等待。daemon 健康、会话队列为空，并不能排除浏览器网络瓶颈。`curl` 使用独立连接池，仍可能立即返回。

只保留需要的订阅，及时关闭用完的弹窗。对于可修改的应用，应跨标签页共享订阅（例如 SharedWorker）、在消费者离开时取消订阅，或让浏览器连接的流端点使用 HTTP/2。需核对实际协商协议：上游使用 HTTP/2，而面向浏览器的代理仍用 HTTP/1.1，不能解除浏览器侧限制。诊断时，结合 Chromium NetLog 的 `SOCKET_POOL_STALLED_MAX_SOCKETS_PER_GROUP` 与当时仍在进行的流响应，区分连接池耗尽和其他阻塞。原始网络日志可能包含业务数据，应私下保管。

`session close --force` 释放指定会话的页面及连接，可让被阻塞的其他会话恢复，但不会修复应用中的重复订阅。agentcloak 不会静默中断业务流，也不会按固定标签页数量自动关闭弹窗。
