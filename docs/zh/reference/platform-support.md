# 平台支持

agentcloak 主要在 **Linux** 上开发和测试，对 **macOS** 和 **Windows** 提供尽力而为的支持。本页说明各平台上哪些功能可用、哪些会降级，以及已知的平台相关限制。

CI 在 `ubuntu-latest`、`windows-latest`、`macos-latest`（Python 3.12–3.14）上运行单元测试套件。浏览器集成测试只在 Linux 上跑——下表反映的是代码层面的平台处理逻辑，以及 CloakBrowser 官方公布的 binary 可用性。

## 功能矩阵

| 功能 | Linux | macOS | Windows | 备注 |
|------|-------|-------|---------|------|
| **CloakBrowser**（默认隐身后端） | 全功能 | 全功能 | 全功能 | binary 按平台自动下载。Linux x86_64/arm64 与 Windows x86_64 是完整的 57 patch 版本（Chromium 146）；macOS arm64/x86_64 是 26 patch 版本（Chromium 145）。macOS 首次启动需经过一次 Gatekeeper 放行（右键 → 打开）。 |
| **Playwright**（降级后端） | 全功能 | 全功能 | 全功能 | 标准 Chromium，无隐身。Linux 上需 `playwright install-deps chromium` 安装系统库（`doctor` 会检查）。 |
| **RemoteBridge**（你的真实 Chrome） | 全功能 | 全功能 | 全功能 | Chrome MV3 扩展与平台无关，无论 daemon 在哪都通过 WebSocket 连回。常见组合：daemon 在 Linux，真实 Chrome 在 Windows/macOS。 |
| **Xvfb / headed 模式** | 按需 | 原生 | 原生 | Xvfb **仅 Linux** 需要，且只在「无 `$DISPLAY` 时跑 headed」的场景自动启动。macOS/Windows 有原生显示服务，headed 模式无需 Xvfb。headless 模式在任何平台都不需要显示。 |
| **humanize**（拟人行为层） | 全功能 | 全功能 | 全功能 | 由 CloakBrowser 提供，与平台无关。headed 模式下最有意义（真实输入事件）。 |
| **httpcloak**（TLS 指纹匹配的 fetch 代理） | 全功能 | 全功能 | 全功能 | 纯 Python 本地代理，无平台分支。代理无法启动时优雅降级为普通 fetch。 |
| **PDF 导出** | 仅 headless | 仅 headless | 仅 headless | Chromium 只能在 headless 模式渲染 PDF——这是**模式**限制而非 OS 限制。`headless = true` 时三平台行为一致。 |
| **剪贴板读取** | headed / bridge | headed / bridge | headed / bridge | Chromium 出于安全在 headless 模式禁止剪贴板*读取*。headed 模式和 RemoteBridge 在所有 OS 上都可用。剪贴板*写入*在所有模式都可用。 |
| **后台 daemon** | 全功能 | 全功能 | 全功能 | POSIX 用 `start_new_session`；Windows 用 `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP`。后台 daemon 默认写轮转日志文件（detached 时看不到 stderr）。 |
| **`doctor --fix`（自动安装）** | 全功能 | 部分 | 不支持 | 各平台都能自动安装 Python 依赖和 CloakBrowser binary。系统包步骤（Playwright 库、Xvfb）仅 Linux，调用你发行版的包管理器；Windows 上 sudo/自动修复路径被跳过，只打印命令。 |

**图例：** *全功能* = 按设计工作 · *按需 / 部分* = 带所注限制工作 · *仅 headless/headed* = 取决于运行模式而非 OS · *不支持* = 该平台不可用。

## 已知平台相关行为

以下是代码中按操作系统分支的位置，汇总于此方便预期：

- **进程存活检查** — POSIX 用 `os.kill(pid, 0)`；Windows 用 `ctypes` 调 `OpenProcess`。两端都能识别过期的 `daemon.pid` 文件。
- **信号处理** — POSIX 上 `SIGINT`/`SIGTERM` 接入优雅关闭。Windows 的 `ProactorEventLoop` 不支持 `add_signal_handler`，所以 daemon 改用 `/shutdown` 路由和此处的 Ctrl-C。
- **权限检查** — `doctor --fix --execute` 在 POSIX 上需要 root（`os.geteuid() == 0`）或 `sudo`；Windows 不走这条路径。
- **用户 spell 目录** — Windows 是 `%APPDATA%\agentcloak\spells`，其它平台是 `~/.config/agentcloak/spells`。
- **Xvfb 自动启动** — 仅在 Linux、headed、无 `$DISPLAY` 时尝试。
- **Playwright 系统库 / Xvfb 检查** — `doctor` 只在 Linux 上探测；macOS/Windows 报告「该 OS 无需」。
- **过期 Chromium 检测** — `doctor` 会标记 CloakBrowser 自动更新遗留的旧 Chromium 版本（每个约 700MB），并打印 `rm -rf` 命令回收。此功能在**所有平台**可用——缓存目录（`~/.cloakbrowser`，或 `CLOAKBROWSER_CACHE_DIR`）用纯 `Path` 操作解析，无 OS 相关代码。
- **会话文件权限** — POSIX 上对 `active-session.json` 应用 `chmod 0o600`（尽力而为，不支持时静默跳过）。

## 建议

- **Linux**：主要目标平台。服务器/CI 跑 headless；只在需要无显示 headed 时装 Xvfb。跑 `cloak doctor --fix` 拉取系统库。
- **macOS**：原生可用。首次经 Gatekeeper 放行 CloakBrowser binary 一次。无需 Xvfb。
- **Windows**：CLI、daemon 和三种后端都可用。优先 headless 模式；用 `/shutdown`（或 `cloak daemon stop`）而非依赖信号关闭。一个常见且支持良好的组合是：daemon 跑在 Linux/WSL，通过 RemoteBridge 驱动你的真实 Windows Chrome。

遇到平台相关 bug 请提 issue，附上你的 OS、Python 版本和 `cloak doctor --detail` 的输出。
