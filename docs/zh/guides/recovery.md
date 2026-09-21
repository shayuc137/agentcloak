# 会话恢复与可靠的浏览器证据

每个会话的请求队列都有上限。等锁沿用 `browser.action_timeout`（毫秒），超时返回 `session_busy` 并指出占用路由。执行也默认受 action timeout 约束，包含快照与截图。显式 navigation/fetch 超时按秒、wait/CDP 按毫秒，可以延长执行预算；清理阶段可能增加短暂且有界的延迟。

```bash
cloak session list --all
cloak session close --force
cloak navigate https://example.com/dashboard --expect-path /dashboard
cloak screenshot --expect-url 'https://example.com/dashboard*' --viewport 1280x800 -o page.png --json
cloak cdp endpoint --page
```

`session list` 展示稳定 ID、可读目录标签、进行中动作和排队数；`--all` 同时展示其他工作空间及规范路径。标签只用于显示，哈希身份仍是命名空间键。HTTP 客户端可在既有 workspace/session 头外提供百分号编码的 `X-Agentcloak-Session-Label` 和 `X-Agentcloak-Workspace-Path`。

本地后端的 `close --force` 绕过普通调度，取消指定会话的请求，终止冻结页面的执行并关闭其标签页。它不重启 daemon，也不关闭其他会话。强制恢复跳过依赖渲染进程的存储采集，保留 workspace context；正常关闭仍会持久化工作空间存储。HTTP 客户端断开后，对应 daemon 请求会取消。RemoteBridge 强制关闭返回 `force_recovery_unavailable`，需使用其正常的放行、重连流程。

页面或浏览器意外消失时返回 `page_recreated` 或 `page_lost`，采集证据前必须重新导航，不能静默使用重建的空白页。`/health` 不生成 snapshot、不替换元素引用，并限制尽力读取页面信息的耗时。

截图 JSON 包含 `url`、`title`、`viewport`（CSS 宽高）、`dpr`、`pixel_width` 和 `pixel_height`（从实际图片解码）。临时视口元数据描述截图时的状态，不是恢复后的视口。输出父目录必须已存在。`--expect-url` 使用区分大小写的 glob；`navigate --expect-path` 精确检查最终 URL 的 pathname，不包含 query 与 fragment。不匹配时失败且不写截图；截图过程中发生导航返回 `page_changed`。

本地后端 `cdp endpoint --page` 返回当前会话的准确页面 target 地址及 `target_id`，不加参数仍返回浏览器级地址。页面选择不构成 CDP 权限隔离。

动作与求值返回 `new_tab`，弹窗累计较多时会提示。仍在加载的弹窗可能先返回 `pending: true` 而没有 tab ID，此时先用 `tab list` 确认。`tab close --others` 只关闭本会话的其他标签页。键名与修饰键别名大小写不敏感；无效组合在输入前拒绝，失败或取消时释放本次新按下的修饰键。`network --since last_action` 包含最近动作自身触发的请求；数字 `--since N` 仍是开区间。route 模式的 `*` 是通配符，`?` 是字面查询分隔符，`*/items?*` 与 `*/items/?*` 的斜杠有区别。

启动时使用 `daemon start --log-level info` 记录请求进入、取锁、开始、完成的时间与 session，覆盖本进程的 `daemon.log_level`。Unix 下 `kill -USR1 <daemon-pid>` 将 asyncio 任务栈写入日志，不需要 ptrace。`cloak version --json`、`/health` 和 `daemon.json` 包含 `build_id`，`--version` 也会打印。内容指纹可区分已安装 wheel 与本地修改；Git checkout 还会显示提交号。
