# 浏览器 profile

Profile 让你跨 daemon 重启保留登录状态、cookie、localStorage、扩展和页面隐藏选择器。每个 profile 是 `~/.agentcloak/profiles/<name>/` 下的一个真实 Chromium 用户数据目录。适用于长期会话（登录态 dashboard、多账号工作流、需要时间积累信任的站点）。

## 快速开始

```bash
cloak profile create work             # 创建空 profile
cloak profile launch work             # 用该 profile 启动 daemon
cloak navigate "https://github.com"   # 一次性登录
# 下次再用 --profile work 启动时，依然处于登录态
```

启动后的 daemon 运行一个绑定到 profile 目录的持久化 Chromium 上下文——cookie、IndexedDB、storage 全都跨重启保留。

## 命令

| 命令 | 用途 |
|------|------|
| `cloak profile list` | 列出所有 profile 及磁盘占用 |
| `cloak profile create NAME` | 创建一个空白 profile |
| `cloak profile create NAME --from-current` | 把当前运行的会话快照成新 profile |
| `cloak profile launch NAME` | 用该 profile 重启 daemon |
| `cloak profile delete NAME` | 删除 profile 目录（不可恢复） |

profile 名必须是 kebab-case（`work`、`personal-gh`、`client-acme-prod`）。CLI 拒绝带空格、大写、标点的名字。

## 把会话存进 profile

不用重做登录流程也能把一次浏览升级成可复用 profile：

```bash
cloak navigate "https://example.com"
# 在带头窗口手动登录
cloak profile create example-account --from-current
```

这会把实时会话的 cookie 和 storage 复制到 `~/.agentcloak/profiles/example-account/`。下次：

```bash
cloak profile launch example-account
cloak navigate "https://example.com/dashboard"   # 已经登录
```

## 用 profile 启动

`profile launch` 会重启 daemon——当前运行中的浏览器会话先被关闭。

```bash
cloak profile launch work                # 前台
cloak profile launch work -b             # 后台
cloak profile launch work --headed       # 强制本次用带头模式
cloak profile launch work --port 18800   # 自定义 daemon 端口
```

也可以通过配置设置默认 profile，每次 daemon 启动都用它：

```toml
# ~/.agentcloak/config.toml
[browser]
default_profile = "work"
```

或环境变量：

```bash
export AGENTCLOAK_DEFAULT_PROFILE=work
```

`default_profile` 设置后，`cloak daemon start`（或任何 auto-start）默认启动该 profile。

## 多账号工作流

常见模式是每个身份一个 profile，按需切换：

```bash
cloak profile list
# { "profiles": [ { "name": "github-personal", ... }, { "name": "github-work", ... } ] }

# 任务 1：个人账号
cloak profile launch github-personal
cloak navigate "https://github.com/notifications"

# 任务 2：工作账号
cloak profile launch github-work          # daemon 在 work profile 下重启
cloak navigate "https://github.com/orgs/acme/projects"
```

因为每次 `launch` 都会重启 daemon，profile 之间互不可见。这是个特性：一个账号写入的 `Set-Cookie` 永远不会泄露到另一个账号。

## 目录布局

```
~/.agentcloak/profiles/
├── work/                     # Chromium user-data-dir
│   ├── Default/              # 标准 Chromium profile 结构
│   ├── First Run
│   ├── hide.json             # cloak hide 管理的选择器
│   ├── cookies-snapshot.json # cookies export 刷新的恢复快照
│   └── ...
├── github-personal/
└── github-work/
```

目录就是标准的 Chromium user-data 目录——可以让常规 Chrome 实例直接打开它检查（`chromium --user-data-dir=~/.agentcloak/profiles/work`）。

## 删除 profile

```bash
cloak profile delete work
```

这会删除整个 `~/.agentcloak/profiles/work/` 目录。daemon 不需要先停——但如果你当前正在运行该 profile，删除会失败，直到你切到别的 profile。

## 恢复 cookie

`cloak cookies export` 不带 `--output` 时会刷新 `cookies-snapshot.json`，同时继续打印 cookie。页面后来丢失登录态时，恢复快照并 reload：

```bash
cloak cookies restore
cloak press Control+r
```

使用 `--file` 可恢复其他快照。无活动 profile 时，默认路径改为 `~/.agentcloak/cookies-snapshot.json`。

## Profile vs cookie 导出

| 需求 | 用法 |
|------|------|
| agent 身份跨多次运行持续保留 | Profile |
| 从真实浏览器一次性迁移登录 | `cloak cookies export` 从 RemoteBridge 导出 → `cloak cookies import` 导入 profile |
| 短期无状态任务 | 不用 profile——默认 ephemeral context 就好 |

cookie 导出路径详见 [Remote Bridge 指南](./remote-bridge.md)。
