# 本地存储

agentcloak 在本机使用两个目录存储数据。本文档说明每个文件的用途、保留时长和磁盘占用。

## ~/.agentcloak/

主数据目录，首次运行时自动创建。典型大小：**< 1 MB**（不含 profiles）。

| 文件/目录 | 用途 | 大小 | 生命周期 |
|----------|------|------|---------|
| `config.toml` | 用户配置 | < 1 KB | 永久保留，用户管理 |
| `daemon.pid` | 运行中的 daemon 进程 ID | < 1 KB | daemon 启动时创建，下次启动时清理残留 |
| `active-session.json` | 当前 daemon session 信息（端口、隐身层级、bridge token） | ~130 字节 | daemon 启动时覆盖 |
| `resume.json` | 上次操作摘要，用于 session 恢复 | < 1 KB | 每次操作覆盖，daemon 停止后保留 |
| `cookies-snapshot.json` | 无活动 profile 时的 cookie 恢复 fallback | 通常 < 100 KB | `cloak cookies export` 不带 `--output` 时覆盖 |
| `logs/` | 预留的日志文件目录 | 空 | daemon 日志输出到 stderr，不写文件 |
| `profiles/` | 保存的浏览器 session，附带 `hide.json`、`cookies-snapshot.json` 和 `localStorage-snapshot.json` | 每个 1-50 MB | 永久保留，`cloak profile delete` 删除 |

### Profile

Profile 是唯一会随使用增长的部分。每个 profile 保存完整的 Chromium 用户数据目录，大小取决于站点复杂度：

- 简单登录 session：约 1-5 MB
- 复杂 SPA 含缓存资源：约 20-50 MB

```bash
cloak profile list          # 查看所有已保存的 profile
cloak profile delete NAME   # 删除指定 profile
```

Profile 没有自动过期或空间限制。

Profile 目录里除了 Chromium user data，daemon 还会自动维护三份辅助文件：`hide.json`（overlay 选择器）、`cookies-snapshot.json`（cookie 快照）和 `localStorage-snapshot.json`（按 origin 分组的 localStorage 快照，版本化 JSON）。以 profile 模式运行时，每次 navigate 会先 dump 当前 origin 的 localStorage、再按目标 origin 恢复，token 刷新和 SPA 登录态因此能跨 daemon 重启保留下来。`cloak profile create --from-current` 也会把 cookies + 当前 origin 的 localStorage 一并写入这两份快照。可选的 `config.toml` overlay 见[配置参考](config.md#profile-级-config-overlay)。

### 日志

daemon 日志输出到 stderr（在终端可见，或被进程管理器捕获）。`logs/` 目录存在但当前未使用。未来版本可能会添加文件日志和自动轮转。

## ~/.cloakbrowser/

CloakBrowser 的补丁版 Chromium 二进制文件。由 `cloakbrowser` 包管理，agentcloak 不直接控制。

| 内容 | 大小 | 生命周期 |
|------|------|---------|
| 补丁版 Chromium 二进制文件及依赖 | **约 200 MB - 1.4 GB** | 首次使用时下载，跨版本升级保留 |

这是磁盘占用最大的部分。回收空间：

```bash
rm -rf ~/.cloakbrowser/
# 下次使用 agentcloak 时自动重新下载
```

## 仅存在于内存中（不写入磁盘）

以下数据保存在 daemon 内存中，daemon 停止时丢失：

| 数据 | 用途 | 限制 |
|------|------|------|
| 环形缓冲区 | 网络事件、控制台日志 | 固定容量 1000 条 |
| Snapshot 缓存 | 每个 tab 的最后一次无障碍树 | 每 tab 1 份 |
| Capture 存储 | 录制的网络流量（`cloak capture start`） | 无限制，直到 `cloak capture stop/clear` |

## 磁盘占用概览

| 位置 | 典型大小 | 会增长？ |
|------|---------|---------|
| `~/.agentcloak/`（不含 profiles） | < 1 MB | 否 |
| `~/.agentcloak/profiles/` | 0 - 500 MB | 会，随保存的 profile 增加 |
| `~/.cloakbrowser/` | 200 MB - 1.4 GB | 否（固定二进制） |
| **总计** | **约 200 MB - 2 GB** | 取决于 profile 数量 |

## 影响存储的配置

| 配置 | 效果 |
|------|------|
| `idle_timeout_min = 30` | 空闲后自动停止 daemon，释放内存资源 |
| `stop_on_exit = true` | CLI 退出时停止 daemon |

当前没有磁盘空间限制、自动清理或日志轮转的配置选项。Profile 需要手动管理。

## 完全清理

```bash
rm -rf ~/.agentcloak/     # 所有配置、profile、运行时文件
rm -rf ~/.cloakbrowser/   # 浏览器二进制缓存
```
