# Project Roadmap & Architecture Decisions

> agentcloak 项目路线图和关键架构决策。
> v0.2.0 架构重构已完成（Phase 5j），当前规划 v0.3.0+ 方向。
> 初始研究完整记录：`.trellis/tasks/archive/2026-05/05-06-reference-projects-research/prd.md`
> v0.2.0 重构记录：`.trellis/tasks/05-15-quality-hardening/`、`spec/guides/architecture-decisions.md`

---

## Core Positioning (D1)

- **Primary**: 通用 agent 浏览器能力层——稳定的"眼睛和手"
- **Secondary**: 日常网站自动化——登录态复用的常见任务
- **Long-term**: 网站能力资产平台——API-first spell 脱离浏览器独立运行
- **Complementary**: jshookmcp 作为 MCP 覆盖 JS Hook/CDP/逆向（不重复实现）

## Tech Stack (v0.2.0)

- **Python 3.12+**：CloakBrowser 隐身浏览器、Playwright-Python、typer CLI、orjson JSON
- **FastAPI + uvicorn**：daemon server，Pydantic v2 request/response model，OpenAPI spec 自动产出
- **httpx**：统一 HTTP client（CLI sync + MCP async），替代 aiohttp
- **BrowserContextBase ABC**：共享行为基类，29 个原子方法，PlaywrightAdapter + RemoteBridgeAdapter
- **CLI primary, MCP optional** (D2)：CLI ~300 tokens on-demand，MCP ~6000 tokens 常驻

## Stealth Architecture

```
Local browser:
  ├─ Default: CloakBrowser (49 C++ patch, humanize, auto-updating binary)
  ├─ Fallback: PlaywrightContext (standard Chromium, no stealth)
  └─ Future: Camoufox (Firefox stealth, different API)

Remote browser:
  └─ RemoteBridge (real Chrome via extension, inherently real fingerprint)

HTTP fetch:
  └─ httpx + httpcloak LocalProxy (TLS fingerprint match CloakBrowser)
```

## Core Abstraction — BrowserContextBase ABC (D6, updated D3)

ABC 基类拥有 ~900 行共享行为（action dispatch、element resolution、feedback collection、frame tracking、browser self-healing），子类只实现 29 个 `_xxx_impl` 原子操作。SecureBrowserContext 作为 decorator 层拦截安全敏感方法。详见 `spec/browser/browser-backend-contract.md`。

## Reuse Strategy (D7)

| Project | Reuse Mode | What |
|---|---|---|
| bb-browser | Port to Python | seq+since model, three-field error envelope |
| OpenCLI | Port to Python | Strategy enum, pipeline DSL |
| CloakBrowser | pip dependency | Stealth backend, humanize layer |
| Scrapling | pip dependency + extract | StealthyFetcher, Cloudflare solver |
| GenericAgent | Extract + rewrite | TMWebDriver connection model |
| pinchtab | Port to Python | Endpoint catalog, IDPI security |

---

## Phase Roadmap (D8)

### Phase 0: Skeleton runs (done)

daemon + CLI + local browser basics

- PatchrightContext as default backend
- daemon (aiohttp), seq counter, tab manager, ring buffer
- CLI (typer), JSON output, three-field error envelope
- Commands: navigate, screenshot, evaluate, snapshot, network, console, tab, doctor
- Deliverable: **agent can open pages, screenshot, execute JS, see network requests**
- Ref: `research/ai-browser-agents.md`（selector_map、seq 模型）, `research/browser-cli-platforms.md`（bb-browser seq+since、三字段 envelope）

### Phase 1: Full interaction (done)

- click/fill/type/scroll/hover/select/press (accessibility-tree `[N]` ref driven)
- `network requests --since last_action` filtering
- `fetch` with cookie (Strategy: COOKIE)
- `profile create/list/launch/delete`
- Deliverable: **agent can interact with pages, reuse login states**
- Ref: `research/ai-browser-agents.md`（browser-use multi_act + terminates_sequence、open-codex --calls-file batch）, `research/browser-cli-platforms.md`（bb-browser/OpenCLI action 集合）, `research/neo-analyzer-wireshark.md`（neo v2 a11y interaction）

### Phase 2: Stealth layer (done)

- [x] CloakBrowser integration (`--stealth`) — pip optional dep, CloakContext subclass
- [x] Xvfb + humanize behavioral layer — XvfbManager auto-spawn/cleanup
- [x] Cloudflare Turnstile bypass — screenX patch extension (Manifest V3, MAIN world)
- [x] httpcloak LocalProxy — tls_only transparent proxy for fetch, graceful degradation
- Deliverable: **agent can bypass most anti-bot detection**
- Ref: `research/browser-stealth.md`（CloakBrowser/httpcloak/Scrapling/Camoufox）, `reference/doc/sop/Cloudflare Turnstile*`、`reference/doc/sop/hCaptcha*`、`reference/doc/sop/滑块拼图*`

### Phase 3: Remote bridge (done)

- [x] Chrome MV3 extension (debugger/cookies/tabs, WS to bridge, CDP command routing)
- [x] Python bridge process (WS hub, extension ↔ daemon routing, auto-reconnect)
- [x] RemoteBridgeContext (BrowserContext Protocol over WS)
- [x] Daemon `/bridge/ws` endpoint + bridge CLI commands
- [x] Bridge config with daemon candidate list auto-probe
- [x] WS token auth (localhost bypass, Bearer token for remote)
- [x] Cookie/session export (`browserctl cookies export`)
- [x] Extension install guidance (first-run auto-open chrome://extensions)
- [x] PyInstaller build spec (`scripts/build_bridge.py`)
- [x] mDNS auto-discovery (optional zeroconf, daemon register + bridge discover)
- Deliverable: **agent operates shayu's real browser remotely**
- Ref: `research/browser-cli-platforms.md`（GenericAgent TMWebDriver WS+HTTP 双传输）, `reference/doc/sop/TMWebdriver*`、`reference/doc/sop/TMWebDriver CDP*`

### Phase 4: Spell platform (done)

> Renamed from "Adapter platform" to "Spell platform" in CLI/MCP audit (Phase 5i prep). Internal code: `@spell` decorator, `SpellRegistry`, `src/agentcloak/spells/`.

- [x] Strategy enum (PUBLIC/COOKIE/HEADER/INTERCEPT/UI) + `@spell` decorator + SpellRegistry
- [x] Pipeline DSL (declarative) + Function (async def) dual mode
- [x] Template engine with `{path}` syntax, 7 built-in pipeline steps
- [x] CaptureStore — full request/response recording with auto-filtering
- [x] HAR 1.2 import/export (to_har/from_har)
- [x] PatternAnalyzer — path parameterization, endpoint clustering, auth detection, schema inference
- [x] SpellGenerator — API pattern → pipeline spell Python code
- [x] `spell list/info/run/scaffold` CLI commands
- [x] `capture start/stop/status/export/analyze/clear` CLI commands
- [x] Daemon `/capture/*` endpoints
- [x] Spell auto-discovery (built-in + user directory)
- Deliverable: **common site operations as one-liner commands**
- Ref: `research/browser-cli-platforms.md`（OpenCLI Strategy enum + pipeline DSL、bb-browser @meta adapter）, `research/neo-analyzer-wireshark.md`（neo schema synthesis + workflow discover）

### Phase 5: Skill + integration (in progress)

- [x] MCP server (10 tools, FastMCP + stdio bridge to daemon)
- [x] jshookmcp CDP coordination (browserctl_cdp_endpoint + /cdp/endpoint)
- [x] Response body capture fix (async handler + 100KB truncation)
- [x] Enhanced /health (stealth_tier, current URL, capture state)

#### Phase 5c: UX + daemon auto-start (done)

- [x] daemon auto-start from MCP (DaemonBridge auto-start on first request)
- [x] MCP spell run tool (agentcloak_spell_run + agentcloak_spell_list)
- [x] CLI cdp endpoint 命令 (`browserctl cdp endpoint`)
- [x] 多 tab 管理 (tab list/new/close/switch — browser + daemon + CLI + MCP)
- Deliverable: **frictionless daemon startup, multi-tab agent workflows**

#### Phase 5d: IDPI 安全层 (done)

- [x] Domain whitelist + blacklist (glob matching, `file://`/`data:`/`javascript:` always blocked)
- [x] Content scan framework (off by default, user-configured regex patterns)
- [x] `<untrusted_web_content>` wrapping for non-whitelisted domains
- [x] SecureBrowserContext wrapper — all backends protected transparently
- Deliverable: **opt-in IDPI three-layer security model**

### Phase 5 施工准则

> **CLI / MCP 能力同步**：每个新增或修改的能力，必须同时暴露到 CLI 命令和 MCP tool。不允许出现"CLI 有但 MCP 没有"或反过来的情况。两者共享 daemon route，保持接口一致。
>
> **Skill 文件跟进**：每个 Phase 完成后，更新 Skill 文件（`skills/agentcloak/SKILL.md` + `.claude/skills/agentcloak/SKILL.md`）反映新能力、变更的参数、废弃的 flag。Skill 是 agent 的主要使用指南，必须和实际能力同步。

#### Phase 5e: 后端重构（隐身层简化）(done)

基于竞品分析（`spec/guides/competitive-analysis.md`）确认的架构变更。

- [x] 移除 Patchright 依赖，CloakBrowser 提升为必装依赖
- [x] `cloak_ctx.py`: `backend="patchright"` 移除（CloakBrowser 默认 playwright）
- [x] `browser/__init__.py`: 默认 tier 改为 CLOAK，humanize=False
- [x] `PatchrightContext` 重命名为 `PlaywrightContext`，import 改为 `playwright.async_api`
- [x] `--stealth` flag 废弃（发 deprecation warning，隐藏 flag）
- [x] 配置文件支持 `humanize = true/false`（config.toml + env var）
- [x] BrowserContext Protocol 增加 `raw_cdp()` 透传方法
- [x] Skill 文件、spec、doctor 命令同步更新
- Deliverable: **CloakBrowser 默认，依赖链简化，Playwright CDP 天花板打开**
- 依据: CloakBrowser 包体积更小（15MB vs 137MB），57 C++ patch 已覆盖 Patchright 驱动层修复，Patchright 反而破坏 proxy auth
- Ref: `reference/CloakBrowser/README.md`（CloakBrowser API + backend 选项 + humanize 配置）, `spec/guides/competitive-analysis.md`（第二层对比 + Patchright 移除决策依据）

#### Phase 5f: Snapshot 增强 + 渐进加载 (done)

snapshot 从扁平列表重写为树形结构，完整提取 ARIA 属性，渐进加载支持大页面。

- [x] Shadow DOM 穿透（`pierce: True`）
- [x] 添加 ARIA 状态输出（expanded、checked、selected、disabled、pressed、invalid、required、focused、hidden）
- [x] 添加输入框当前值（value/valuetext/valuemin/valuemax/valuenow）
- [x] 密码字段脱敏（`value="••••"`）
- [x] 零宽字符清理（BOM、ZWS、ZWNJ、ZWJ、word joiner）
- [x] StaticText 聚合去重
- [x] 扩充 INTERACTIVE_ROLES（+6: dialog、alertdialog、grid、listbox、tree、menu）
- [x] 新增 CONTEXT_ROLES（toolbar、tabpanel、figure、table、form、status、alert 等 14 个）
- [x] 缩进树形输出（2 空格，childIds 重建父子关系）
- [x] generic 节点折叠（无名 + 单子节点 → 提升）
- [x] compact 模式压缩树形（自底向上祖先保留算法）
- [x] 渐进加载：daemon 缓存完整 snapshot + 节点级截断 + 摘要目录
- [x] `--focus=N` 子树展开（含祖先面包屑）
- [x] `--offset=N` 分页
- [x] `--max-nodes=N` 统一截断控制（节点级，默认 150/80）
- [x] DRY 重构：accessible/compact 合并为统一 `_build_snapshot()`
- [x] Skill 文件更新
- [x] CLI / MCP / daemon 新参数同步
- Deliverable: **agent 页面感知能力对齐竞品水平，大页面可渐进探索**
- 推后项: ref 版本号机制、`*[N]` diff 标记、iframe 嵌套、cursor:pointer 发现、link URL 提取
- Ref: `reference/agent-browser`（@eN ref + 缩进树形 + 200-400 token）, `reference/pinchtab`（eN + diff + token 预算）, `spec/guides/competitive-analysis.md`（第三层对比 + 渐进加载设计）

#### Phase 5g: 交互补齐 + Proactive State Feedback (done)

Playwright API 已支持，暴露到 BrowserContext Protocol → daemon → CLI/MCP 链路。

- [x] Proactive State Feedback 机制（action 返回值主动包含 pending_requests/dialog/navigation/download/current_value）
- [x] 对话框处理（`page.on("dialog")` — alert/beforeunload 自动 accept，confirm/prompt 暂存等 agent 处理）
- [x] 条件等待（`wait --selector/--url/--load/--js/--ms`，直接映射 Playwright API）
- [x] keyboard 组合键（`press --key "Control+a"` Playwright 原生 `+` 语法 + `keydown`/`keyup` 独立命令）
- [x] 文件上传（`upload --index N --file path`，Playwright `set_input_files()`）
- [x] frame 切换（`frame list` / `frame focus --name/--url/--main`，Playwright Frame API）
- [x] 高危操作日志（evaluate/upload 审计日志，structlog）
- [x] 批量模式增强（dialog 中断 + read-after-write settle + wait 作为 batch step）
- [x] Config 扩展（action_timeout/batch_settle_timeout，env var 覆盖）
- [x] Skill 文件更新：新增交互命令用法、feedback 机制、dialog/wait/upload/frame
- [x] CLI / MCP / daemon 同步：14 个新 MCP tool，4 个新 CLI command group，6 个新 daemon route
- Deliverable: **交互覆盖补齐，proactive state feedback，agent 操作流畅性大幅提升**
- 推后项: network route 拦截（`page.route()`）、drag & drop、剪贴板
- Ref: `reference/agent-browser`（54+ commands, wait/dialog/frame/keyboard）, `reference/GenericAgent`（对话框抑制 + CSP 剥离实现参考）, `spec/guides/competitive-analysis.md`（第四/五/六层对比）, `spec/guides/proactive-state-feedback.md`（设计原则）

#### Phase 5h: RemoteBridge 能力对齐 + 共享层重构 (done)

- [x] 共享 Snapshot Builder：`_snapshot_builder.py` 抽取，两端复用同一套树构建/compact/ARIA/渐进加载逻辑
- [x] RemoteBridge snapshot 对齐：compact 模式、ARIA 状态、渐进加载、backendDOMNodeId 精确元素定位
- [x] Extension 可靠性：chrome.alarms keepalive、双执行路径（scripting + CDP fallback）、CDP navigate 等待、状态持久化
- [x] RemoteBridge action 补齐：scroll/hover/select/dialog/wait/upload/frame 全部 CDP 实现
- [x] 多 Frame AX Tree 合并：`--frames` 参数，iframe 内容自动嵌入 snapshot 树（两端）
- [x] Snapshot Diff：`--diff` 参数，标记 `[+]` 新增 / `[~]` 变更 / removed 摘要（两端）
- [x] includeSnapshot：action 返回可选附带 compact snapshot（daemon 层，两端）
- [x] $N Batch 引用：batch 命令支持 `$N.path` 结果引用（daemon 层，两端）
- [x] Stale Ref 自动重试：element_not_found 自动 re-snapshot + 重试一次（daemon 层，两端）
- [x] 统一端口范围：daemon 默认端口 9222→18765，与 bridge 共享 18765-18774
- [x] 模式自适应连接：Extension 同时发现 daemon/bridge，优先 daemon 直连；daemon 新增 `/ext` endpoint
- [x] Tab Claiming：`bridge claim` 接管用户已打开的标签页
- [x] Tab Group：agent 操作的 tab 自动归入蓝色 "agentcloak" Chrome tab group
- [x] Session Finalize：`bridge finalize` 三种模式（close/handoff/deliverable）
- [x] Skill 文件更新
- Deliverable: **RemoteBridge 生产可用，共享层消除重复，通用增强两端受益**
- 推后项: jshook 松耦合（另开 Skill），Camoufox 后端，network route 拦截，drag & drop，剪贴板
- Ref: `reference/chrome-devtools-mcp`（CLI 自动生成、Skill 拆分、includeSnapshot）, `reference/open-codex-browser-use`（tab group、session lifecycle）, `reference/pinchtab`（eN ref、snapshot diff、multi-frame）, `reference/GenericAgent`（alarms keepalive、双执行路径、$N batch）

#### Phase 5i: 工程基础 + 接口审查 + 发布

CI / 工程基础 (done)：
- [x] GitHub Actions CI（ruff + pyright + pytest unit 3.12/3.13 matrix + integration + build + pip-audit）
- [x] MIT LICENSE 文件
- [x] 全量 lint/format 清理（120+ 文件）

CLI / MCP 接口审查 (done)：
- [x] `open` → `navigate`（MCP 生态共识对齐）
- [x] `js execute-js` → `js evaluate`（MCP 生态共识对齐）
- [x] `adapter`/`site` → **spell** 全链路重命名（内部代码 + 外部接口 + 文档，cloak+spell 品牌）
- [x] 删除 `browser state`（被 snapshot+screenshot+network 完全覆盖，Phase 0 遗留）
- [x] readOnlyHint 确认已标注（所有 MCP 工具）
- [x] `scripts/check_consistency.py` 自动化一致性校验（34 routes, 23 MCP tools, 18 CLI groups）集成 CI

文档与发布：
- [x] README 拆分（787→237 行，docs/en/ + docs/zh/ 双语 30 文件）
- [x] Skill 文件拆分（主文件精简，详细内容拆到 `references/` 按需加载）
- [x] SECURITY.md + CONTRIBUTING.md
- [x] navigate --snapshot（observe-act loop 优化）
- [x] 依赖简化（httpcloak + mcp 移入基础依赖，pip install 一步到位）
- [x] headless 配置项（config.toml + env var）
- [x] 本地存储文档（docs/reference/local-storage.md）
- [x] PyPI v0.2.0 发布 + GitHub Releases（已发布至 v0.2.3）
- [x] 日志文件写入（后台 daemon 模式日志持久化 + 轮转）

推后项：
- Demo 素材（GIF / asciinema）
- 稳定性矩阵（各后端 × 各平台支持状态表）
- Docker 分发（基于 `cloakhq/cloakbrowser`）
- daemon + 真实浏览器集成测试（CI 里配浏览器环境成本高，手动验证够用）
- 错误恢复测试（推到用户反馈驱动）

- Deliverable: **clean API surface, CI, consistency checks, ready for v0.2.0 release**
- Ref: `research/neo-analyzer-wireshark.md`（Wireshark-MCP installer 模式）

#### Phase 5j: v0.2.0 架构重构（done）

dogfood v0.2.0-pre 暴露系统性重复，brainstorm 出 D1-D5 决策，9 个 task T1-T9 落地。任务目录：`.trellis/tasks/05-15-quality-hardening/`。

- [x] **D1: aiohttp → FastAPI + uvicorn** — 37 个 route handler 全部带 Pydantic request/response model，`/openapi.json` 自动暴露给半自动生成消费
- [x] **D2: 统一 httpx client** — 合并 `cli/client.py` + `mcp/client.py` 为 `agentcloak.client.DaemonClient`，sync/async 双方法对，消除 15 份 `asyncio.run()`，五种 httpx 异常一对一映射到结构化 error code
- [x] **D3: BrowserContext Protocol → ABC** — `BrowserContextBase` 拿走 ~900 行共享行为（action dispatch、batch、wait、upload、dialog、self-healing），子类只实现 29 个 `_xxx_impl` 原子操作
- [x] **D4: 半自动生成** — `scripts/generate_skill.py --check` 校验 Skill 命令段落和 OpenAPI spec 同步，`scripts/check_surface_count.py` 校验 daemon route 数 = CLI command 数 = MCP tool 数
- [x] **D5: bridge 迁移** — `aiohttp.web` → Starlette + `websockets` 库（uvicorn 底层已有的依赖）
- [x] **Service 层提取** — `daemon/services/` 拿走 stale-ref retry、snapshot diff、profile CRUD、capture export、doctor 检查；route handler 变薄 HTTP 壳
- [x] **配置统一** — 25+ 处硬编码 magic number 全部走 `AgentcloakConfig`，FastAPI `Depends(get_config)` 注入
- [x] **错误信封统一** — 所有路径走 `register_exception_handlers()`，profile CRUD 不再绕过 `_ok()`
- [x] **遗留清理** — `--stealth` flag、patchright compat mapping、stale "Adapter" 术语全部清除
- [x] **浏览器自恢复** — `_check_browser_alive()` + `_looks_like_browser_closed()` heuristic，下次请求拿结构化 `browser_closed` 错误而非 raw Playwright exception
- [x] **snapshot link href** — `_snapshot_builder` 提取 link URL 输出到 a11y tree
- [x] **CI consistency check** — `scripts/check_surface_count.py` 集成 CI

Deliverable: **v0.2.0 ready — 系统性重复消除，OpenAPI 单一事实来源，新增能力只写一处定义**

依据: dogfood 报告（`.trellis/workspace/shayu/dogfood-v0.2.0-pre-release.md`），架构审计（`.trellis/tasks/05-15-quality-hardening/research/architecture-audit.md` 13 个结构性问题）。
spec 更新：`browser/browser-backend-contract.md`（Protocol → ABC）、`deps/fastapi.md`（新增）、`deps/httpx.md`（新增）。

---

## v0.3.0+ Roadmap

> **排序原则：** 没有外部用户之前，先夯实底层——工程质量 → 稳定性 → 能力补全 → 平台进阶 → 生态。底层投资的性价比最高，后续每加一个功能都会受益。

### Phase 6: 工程强化

> 消除当前最大的维护成本瓶颈，让后续新增功能的边际成本趋近于零。

#### 6a: 架构深化 (done)

> 基于 v0.2.3 架构审计（2026-05-19），4 个 step 顺序完成。

- [x] **Step 1: 清理隐式耦合** — 22 处 app.state → Depends 注入，resume_snapshot() 修复 abstraction leak，BridgeService 提取（290 行）。bridge handler 从 ~110 行瘦到 26 行
- [x] **Step 2: routes + models 拆分** — routes.py（1329 行）→ 7 个文件（≤300 行），models.py（599 行）→ 10 个文件。修复模块级 load_config() 冻结问题
- [x] **Step 3: text_renderers 移层 → CLI/MCP 共享渲染** — text_renderers 移到 core/，CLI 本地渲染，MCP 统一调 text_renderers，screenshot 返回 ImageContent。daemon 只返回 JSON，DaemonClient 移除 Accept: text/plain 代码路径（-174 行）。agent-first 渲染原则
- [x] **Step 4: DaemonClient 瘦身** — 移除 32 个未使用 sync 方法，1464 → 1227 行。9 个 CLI 特殊 sync + 40 个 MCP async 保留
- [x] 层隔离例外文档化（AGENTS.md）
- Deliverable: **daemon 内部耦合清理、文件组织合理化、CLI/MCP 共享渲染、DaemonClient 瘦身完成**

#### 6b: 测试覆盖增强 (done)

> 基于架构审计精确盲区列表，新增 52 个 mock-based 测试。

- [x] B1: CLI CliRunner 端到端（13 tests）— navigate/snapshot/click/tab/status/config 的 text + json mode
- [x] B2: RemoteBridgeContext CDP action（9 tests）— click/fill/scroll/hover/resolve 的 CDP 命令序列
- [x] B3: SecureBrowserContext（11 tests）— whitelist/blacklist/content_scan/untrusted/passthrough
- [x] B4: BridgeService lifecycle（9 tests）— 连接互斥/token 验证/断连清理/重连
- [x] B5: Remote capture state machine（4 tests）— CDP Network 事件序列/body 截断/失败处理
- [x] B6: CloakContext launch 参数（6 tests）— proxy/extra_args/DoH/humanize/headless
- Deliverable: **493 → 611 unit tests，6 个盲区全部有自动化回归保护**

#### Bug fixes from real usage (done)

> 来源：DOS-web 项目 + 种子用户真实反馈。

- [x] **P0: navigate 失败后 silent failure** — `_page_valid` flag + `_check_page_valid()` guard，navigate 失败后后续操作明确报错而非静默跑旧页面
- [x] **doctor 输出优化** — 默认 2 行精简摘要 + 运行状态，`--detail` 保留详细输出。`browser_description()` 方法让后端自报名字版本

**待补文档（功能已有，文档不够明确）：**
- [x] wait recipe：文档补充"等 Web Font 加载完后截图"高频场景
- [x] screenshot format：文档明确默认 JPEG + `--format png` 用法 + "UI 设计验证建议 PNG" 提示

#### 6d: Config 拆分 + 浏览器层去重 (done)

> 架构审计发现的独立优化项。任务目录：`.trellis/tasks/05-19-config-split-dedup/`。

- [x] `AgentcloakConfig` 36 字段拆为 `DaemonConfig`（9）/ `BrowserConfig`（18）/ `SecurityConfig`（4）/ `BridgeConfig`（2）聚合根
- [x] `base.py` 构造时接收 `BrowserConfig`，5 处运行时 `load_config()` 归零
- [x] `config_writer.py` 评估结论：保持独立（只有 config_cmd.py 消费，职责单一）
- [x] `build_capture_entry()` 工厂函数提取到 `core/capture.py`，两端共享 body 截断 + content-type 过滤
- [x] `_dispatch_dialog_event()` 具体方法提升到 base，`_auto_accept_dialog_impl()` 新增为第 30 个抽象方法
- [x] `dump_config()` 通过 `_FLAT_FIELD_MAP` 保持 `cloak config list` 输出向后兼容
- Deliverable: **每层只看自己的配置、浏览器后端共享逻辑集中、611 测试通过**

#### 6e: Snapshot 视觉遮挡检测

> 行业首创：利用 DOM bounding rect 批量查询识别视觉重叠节点，解决 CSS 3D 翻转卡片、carousel clone 等场景的 a11y 重复。来源：winnable.gg Senja 翻转卡片组件（front/back 两面 DOM 级重复，Chrome a11y 不认识 `backface-visibility: hidden`）。竞品（agent-browser / OpenCLI / chrome-devtools-mcp）均未解决此问题。

- [ ] `base.py` `_build_tree_snapshot` 增加 bounding rect 批量获取（一次 evaluate 调用，传 backendDOMNodeId 列表）
- [ ] `SnapshotNode` 增加 `rect` 可选字段
- [ ] Phase 2 新增遮挡检测 pass：rect 完全重叠的兄弟节点，保留内容更多的那个
- [ ] 边界条件：interactive 节点不参与遮挡剪枝、rect 差异容忍阈值（亚像素偏差）
- [ ] 前置：Phase 6 `snapshot-dedup-refactor` 三阶段管道（已完成，SnapshotNode + Phase 2 pass 扩展点就绪）
- Deliverable: **翻转卡片等视觉遮挡导致的重复消除，竞品无此能力**

#### 6c: 平台兼容 + 可靠性 + Agent DX

**为什么做：** 最近三个 commit 都是 Windows 兼容修复（PID check / detached daemon / geteuid guard），说明跨平台需求真实存在。daemon 长期运行的崩溃恢复也缺失。DOS-web 反馈补充了 agent 脚本友好度问题。

- [ ] Windows / macOS 平台支持矩阵文档（哪些功能可用、哪些需降级、已知限制）
- [ ] 平台相关 CI 测试（GitHub Actions matrix 增加 windows-latest / macos-latest smoke test）
- [ ] daemon 崩溃恢复：CLI/MCP 检测到 daemon 挂了自动重启（当前只有首次 auto-start，没有 re-start）
- [ ] httpx 连接断线重试：CLI → daemon 的请求增加可配置的 retry（网络抖动、daemon 重启间隙）
- [ ] daemon 健康 metrics 增强：uptime、请求计数、当前连接数、浏览器内存用量
- [x] ~~`screenshot --output <path>` 指定保存路径~~ ← 已在 Phase 7a 完成
- [x] 错误处理全量审计：全部 99 route + 5 个 browser manager + 3 个 backend 确认完毕——无静默失败、三字段 envelope 完备（27:27 完美 1:1）、`_cdp_send_impl` 缺失包装已修复（commit `6d6f2b8`）
- [x] CLI 全量 dogfood：37 命令组 × 真实站点（github/binance/w3schools/graphql），4 bug + 3 UX 已修，零残留 blocking issue
- Deliverable: **三平台可用，daemon 长期运行可靠，连接中断自动恢复，错误处理无盲区**

#### 6f: Dogfood 遗留优化

> 来源：CLI full-surface dogfood（2026-05-20）发现的体验改善项。

- [ ] **upload 自动查找隐藏 file input** — `upload -f photo.jpg` 不指定 `--index` 时自动 `querySelectorAll('input[type=file]')` 查找（含 `display:none`），支持 `--nth N` 选择第 N 个。解决现代站 drag-drop 上传无可见 file input 的问题
- [ ] **navigate 后自动 WS/SSE 监听** — 和 console CDP 同模式，navigate 尾部调 `streaming_monitor.ensure_listening()` 避免早期 WS 连接丢失
- [ ] **download wait --click N** — arm waiter → click [N] → await download，一条命令完成点击触发下载。解决 agent 单线程无法并发 wait + click 的问题
- [ ] **sourcemap 404 错误信息优化** — fetch 返回非 JSON（HTML 404）时报 "sourcemap fetch returned non-JSON (status 404)" 而非 "parse failed"
- Deliverable: **agent 真实场景下的四个常见摩擦点消除**

### Phase 7: 浏览器能力补全 + 网页逆向

> 补齐 agent 常见操作场景的缺失能力，同时吸收轻量网页逆向能力，让 agentcloak 独立覆盖 90%+ 的网页逆向场景。
> **战略决策 D23**：agentcloak 吸收网页逆向核心能力（CDP 原生），jshookmcp 退守非浏览器逆向（Frida/Ghidra/TLS/系统调用/移动端）。详见 Key Design Decisions 段。
> 调研记录：`.trellis/tasks/05-20-05-20-core-capabilities/research/core-capabilities-survey.md`

#### 7a: 基础能力补全 (done)

- [x] **Console 日志捕获** — ring buffer + seq/since 模型，level 过滤，uncaught exceptions 分流，终端文本消毒
- [x] **下载管理** — 双模式（URL 直下 + 点击触发），SSRF guard（精确 blocklist，fake-IP 198.18/15 放行），IDPI 域名检查
- [x] **Cookies 完整 CRUD** — set/clear/delete + `--curl` 从 Copy-as-cURL 导入
- [x] **Storage 读写** — localStorage/sessionStorage get/set/delete/clear（via evaluate，JS 注入防护）
- [x] **剪贴板** — read/write + CDP permission grant（headless 下 read 受 Chromium 安全限制，headed/bridge 可用）
- [x] **PDF 导出** — page.pdf() 含 format/landscape/scale/margin 参数（仅 headless 支持）
- [x] **`cloak serve <dir>`** — 内嵌 Starlette 静态文件 server，端口自动分配，daemon 关闭时自动停止
- [x] **`screenshot --output <path>`** — 指定截图保存路径
- Deliverable: **Routes 41→59, MCP 23→29, CLI 21→27, Tests 611→701。+5226 行**
- 已知限制: clipboard read headless 不可用（Chromium 安全策略）; PDF 仅 headless 支持
- 任务记录: `.trellis/tasks/archive/2026-05/05-20-7a-core/`

#### 7b: 网页逆向能力 (done)

> **战略定位变更**：agentcloak 从"浏览器自动化工具"扩展为"浏览器自动化 + 网页逆向工具"。吸收 jshookmcp 中 CDP 原生可达的网页逆向能力（~53 tools across 6 domains），让 agent 只开一个 MCP/CLI 就够用。
> 重型逆向（Frida/Ghidra/TLS key log/AST 反混淆/系统调用/WASM/内存/进程/移动端）留给 jshookmcp（~250 tools across 25+ domains）。Transform（JS 反混淆）靠 debugger + sourcemap + LLM 组合覆盖，不引入 Node.js 依赖。
> 架构设计：brainstorm 决策 D-Q1~Q7，详见 `.trellis/tasks/05-20-7b-reverse-engineering/design.md`
> 调研：`.trellis/tasks/05-20-7b-reverse-engineering/research/`（ABC 扩展分析 + CDP 跨后端矩阵 + 竞品架构模式）

**架构决策摘要（brainstorm D-Q1~Q7）**：
- Manager 放 browser 层（base 成员），多浏览器切换天然隔离
- Route 拦截走双路径（Playwright API + Bridge CDP），延续现有 `_impl` 模式
- Manager 通过 base 薄接口（`_cdp_send`/`_on_cdp_event`/`_cdp_enable_domain`）间接操作 CDP，统一审计/安全收口
- Navigate 后直接调用各 manager `on_navigated()`；按 manager 独立 lazy init + navigate 不 disable 域 + 切 tab 才 reinit（照搬 js-reverse-mcp 生产验证策略）
- Debugger 暂停时拒绝不兼容操作（结构化错误，和 dialog_blocked 同模式）
- 基础 antidebug 挂在 DebuggerManager（`setSkipAllPauses` + 预设 init script），高级留后续

**子任务拆分（T0+T1 可并行，T2/T3 依赖 T0，T4 依赖 T3）**：

T0: CDP 基础设施（持久 session + base 薄接口 + Bridge Extension 按需 enable）
- [x] PlaywrightContext 持久 CDP session 缓存层（per-page，不再用完即 detach）
- [x] base.py 新增 `_cdp_send`/`_on_cdp_event`/`_cdp_enable_domain` 三个薄接口
- [x] RemoteBridgeContext CDP 接口实现 + feed_message 扩展
- [x] Bridge Extension 按需 enable CDP 域机制（从 Phase 7e 提前，7b 的硬前置）

T1: 轻量逆向能力（不依赖持久 session，可与 T0 并行）
- [x] **ScriptManager** — init script add/remove/list + hook 预设模板（fetch/XHR/JSON.parse/crypto/timing 劫持）
  - route: `/script/add`, `/script/remove`, `/script/list`
- [x] **Header 注入** — Playwright `set_extra_http_headers()` / CDP `Network.setExtraHTTPHeaders`
  - route: `/emulation/headers`
- [x] **RouteManager** — abort/fulfill/continue 三态 + resourceType/method/status 过滤 + per-tab + unroute
  - route: `/route/add`, `/route/remove`, `/route/list`
- [x] **GraphQL** — introspection/查询/重放。纯 fetch + evaluate 组合
  - route: `/graphql/introspect`, `/graphql/query`

T2: Streaming 监控（依赖 T0）
- [x] **StreamingMonitor** — WS 连接/帧捕获 + SSE 事件监控。ring buffer + seq/since 模型
  - route group: `/ws/*`, `/sse/*`

T3: Debugger（依赖 T0）
- [x] **DebuggerManager** — enable/disable + 断点（URL/XHR）+ 单步 + 调用栈 + scope 变量 + 断点上下文求值 + 源码获取/搜索 + 基础 antidebug（`setSkipAllPauses` + 预设 init script）
  - route group: `/debugger/*`
- [x] 暂停态阻塞机制：不兼容操作返回 `debugger_paused` 结构化错误

T4: SourceMap（依赖 T3）
- [x] **SourceMap** — scriptParsed 收集 + .map 下载 + 纯 Python VLQ 解码 + 位置反查 + 源码树重建
  - route group: `/sourcemap/*`

> Hooks / Antidebug 未拆为独立 route：JS hook 走 ScriptManager 的 5 个预设（fetch/xhr/json_parse/crypto/timing），基础 antidebug 走 DebuggerManager 的 `skip-pauses`（`setSkipAllPauses`）。

- Deliverable: **agentcloak 完整替代 jshookmcp 的 debugger/sourcemap/streaming/graphql/hooks/antidebug 6 个域 ~53 tools，独立覆盖 90%+ 网页逆向场景。Routes 59→92, MCP 29→36, CLI 27→35, Tests 701→855**
- 不做（留给外部工具）：Proxy（→ mitmproxy CLI）、Transform/AST 反混淆（→ debugger+sourcemap+LLM 组合，极端场景 `npx webcrack`）、Network 底层（→ dig/scapy/tshark）、Canvas 游戏引擎逆向（太特化）、Protocol analysis（非浏览器）、内存/二进制/TLS/WASM/进程/移动端（→ jshookmcp ~250 tools）
- Ref: `reference/js-reverse-mcp`（debugger + CdpSessionProvider + lazy init 范本）, `reference/chrome-devtools-mcp`（同源 collector 架构）, `reference/pinchtab`（route 三态）, `reference/jshookmcp`（能力边界参考，36 域 409 tools）
- 任务记录: `.trellis/tasks/05-20-7b-reverse-engineering/`

#### 7c: 反检测增强

- [ ] 用户自定义 Chrome 扩展加载配置（`[browser] extensions = ["/path/to/ext"]`）— 底层已支持（Turnstile patch 就是扩展），缺用户侧配置入口
- [ ] 简单 CAPTCHA 自动解决探索：agent 通过 `--frames` 识别 Turnstile/reCAPTCHA iframe → frame focus → humanize click
- [ ] CAPTCHA 检测（识别页面上的验证码类型：Turnstile/reCAPTCHA/hCaptcha/slider）— 参考 jshookmcp `captcha_detect`
- [ ] CAPTCHA 等待（等待验证码出现/完成）— 参考 jshookmcp `captcha_wait`
- [ ] 第三方 CAPTCHA solver 扩展集成调研（2captcha、hCaptcha solver 等）
- [ ] 隐身验证工具（检测当前 CloakBrowser 隐身是否生效：fingerprint 一致性检查）— 参考 jshookmcp `stealth_verify`
- Deliverable: **用户可加载自定义扩展，CAPTCHA 检测+处理有明确路径，隐身可验证**

#### 7d: 进阶交互 + 设备模拟

- [ ] drag & drop（扩展现有 `/action` 的 kind: `"drag"`）
- [ ] multi-session（同时管理多个站点的独立 context，支持多浏览器同时运行 + 切换）
  - route: `/session/create`, `/session/list`, `/session/switch`, `/session/close`
  - 7b manager 架构已为此预留：每个 ctx 各自持有 manager 实例，切换零额外适配
- [ ] viewport / device emulation — CDP `Emulation.setDeviceMetricsOverride` + 预设设备 profile（iPhone/iPad/Android）
  - route: `/emulation/viewport`, `/emulation/device`
  - 参考 jshookmcp `page_set_viewport` / `page_emulate_device`
- [ ] IndexedDB 读取 — CDP `IndexedDB` 域或 evaluate 注入
  - route: `/storage/indexeddb`
  - 参考 jshookmcp `indexeddb_dump`
- Deliverable: **进阶自动化场景覆盖，设备模拟，IndexedDB 访问**

#### 7f: 逆向辅助——Performance / Profiling (done)

> 7b `_cdp_send` 基础设施就绪后，这些 CDP 域能力可低成本接入。代码覆盖率对逆向定位关键代码价值最高。

- [x] **JS 代码覆盖率** — CDP `Profiler.startPreciseCoverage` / `stopPreciseCoverage` / `takePreciseCoverage`。执行一个操作后看哪些 JS 函数/行被执行了，快速定位关键代码
  - route: `/profiler/coverage/start`, `/profiler/coverage/stop`, `/profiler/coverage/get`
- [x] **CPU Profiling** — CDP `Profiler.start` / `stop`。JS 执行时间分布，找耗时函数（通常是加密/签名）
  - route: `/profiler/cpu/start`, `/profiler/cpu/stop`
- [x] **性能指标** — CDP `Performance.getMetrics`。DOM 节点数、JS heap、Layout 次数等
  - route: `/performance/metrics`
- [x] **内存快照** — CDP `HeapProfiler.takeHeapSnapshot`。查找内存中的密钥/token/解密数据
  - route: `/profiler/heap/snapshot`
- Deliverable: **逆向辅助工具补齐，代码覆盖率帮助快速定位关键代码。Routes 92→99, MCP 36→38, CLI 35→37, Tests 855→908**
- 参考: jshookmcp `performance_get_metrics` / `performance_coverage` / `profiler_cpu` / `profiler_heap_sampling`
- 任务记录: commit `7482734`

#### 7e: Bridge 体验强化

> RemoteBridge（Chrome Extension）从"能用"到"好用"。7b 逆向能力完成后统一补齐 bridge 端缺口。

**能力同步**：
- [x] ~~CDP 域按需 enable（agent 请求 debugger/fetch/network 时动态开启，当前只有 Page + Runtime）~~ ← 已提前到 Phase 7b T0（7b 的硬前置）
- [ ] 7a/7b 新能力 bridge 端对齐验证
- [ ] 双端能力矩阵文档（本地 vs bridge 支持状态表）

**Extension 优化**：
- [ ] MV3 service worker 生命周期管理增强（状态恢复边缘 case）
- [ ] 配置 UX 改善（options page 美化 + mDNS 自动发现入口）
- [ ] Extension 安装引导优化（首次 onboarding 流程）

**使用体验**：
- [ ] 断连 → CDP debugger reattach 自动恢复
- [ ] batch 命令 WS 往返优化（减少延迟）
- [ ] bridge finalize 三种模式 UX 打磨

**权限控制**：
- [ ] per-tab 控制（允许/禁止特定 tab 被操控）
- [ ] per-domain 控制（Extension 层域名白名单，补充 IDPI）
- [ ] 敏感操作确认（agent 执行危险操作时 Extension 弹确认）

- Deliverable: **Bridge 从"能用"到"好用"，双端能力对齐，权限精细控制**

### Phase 8: Spell 平台进阶

> 核心理念：**API-first, UI-fallback**（参考 neo 的双通道设计）。spell 应该能脱离浏览器独立运行，只在必要时才开浏览器。
> **前置条件：** Phase 6a 的 client 自动生成和 Phase 7a 的能力补全完成后，spell 平台有更完整的底层支撑。

#### 8a: Spell 独立运行（不依赖浏览器）

- [ ] Spell credentials 持久化（cookies/headers 序列化到 profile 目录）
- [ ] Spell 运行时 credentials 有效性检测（HTTP 401/403 → 标记过期）
- [ ] credentials 过期时自动触发浏览器刷新（开浏览器 → 登录 → 更新 credentials → 关浏览器）
- [ ] httpcloak preset 从 CloakBrowser 版本自动同步（`CHROMIUM_VERSION` → `chrome-{major}`）
- [ ] 无浏览器 fetch 模式（PUBLIC + httpcloak TLS 指纹，不需要浏览器提供 cookies）
- Deliverable: **spell 默认走 API 不开浏览器，credentials 过期自动刷新**
- Ref: `research/neo-analyzer-wireshark.md`（neo API-first + live header re-fetch）

#### 8b: Workflow Discover + OpenAPI 导出

- [ ] `capture workflow discover` — 从 captured traffic 自动识别多步操作流程（登录→搜索→翻页→提取）
- [ ] `capture schema openapi` — 从 captured API patterns 导出 OpenAPI 3.1 spec
- [ ] Spell auto-generation 闭环：capture → analyze → generate spell → spell 可独立运行
- [ ] Auth header redaction at capture time（安全持久化，只存 header names，replay 时 re-fetch）
- Deliverable: **从浏览操作自动沉淀为可复用的 API spell**
- Ref: `research/neo-analyzer-wireshark.md`（neo schema generate / openapi / flows / deps / workflow discover）

#### 8c: CLI/MCP 全自动生成

在 Phase 6a DaemonClient 生成器基础上，推进到 CLI 和 MCP 也自动生成。

- [ ] CLI typer 命令从 OpenAPI 完全自动生成（当前是手写薄适配层）
- [ ] MCP FastMCP 工具从 OpenAPI 完全自动生成（当前是手写薄适配层）
- [ ] 评估甜蜜点：表面特有逻辑（`--pretty` flag、screenshot base64、Skill 叙述）能否无副作用拆出
- Deliverable: **新增 daemon route 后零手写，CLI/MCP/Skill 全自动对齐**

### Phase 9: 生态与集成

> 有用户基础后再推进的方向。优先级由实际需求驱动。

#### 9a: 分发与社区

- [ ] 多平台分发配置（.claude-plugin, .mcp.json, gemini-extension.json 等）
- [ ] Spell 社区共享（用户贡献 spell 发布 + 安装 + 版本管理）
- [ ] Docker 分发（基于 `cloakhq/cloakbrowser`）
- [ ] Demo 素材（GIF / asciinema / 短视频）

#### 9b: jshook 协作优化

> Phase 7b 完成后，agentcloak 独立覆盖网页逆向，jshookmcp 退守非浏览器逆向（Frida/Ghidra/TLS/系统调用/移动端/Canvas 游戏引擎）。两者仍通过 CDP endpoint 共享协作，但 agent 日常只需 agentcloak 一个工具。

**agentcloak 完整替代的 jshookmcp 域（7b 完成后）**：
`debugger`(16), `sourcemap`(6), `streaming`(5), `graphql`(6), `hooks`(2), `antidebug`(2) + `browser` 域大部分(~45/63) + `network` 域部分(~15/37) = **~97 tools**

**jshookmcp 保留的域（非浏览器逆向，CDP 不可达，~250 tools）**：
`memory`(30), `boringssl-inspector`(28), `binary-instrument`(23), `analysis`(20), `process`(17), `protocol-analysis`(16), `platform`(14), `wasm`(12), `proxy`(8), `syscall-hook`(7), `adb-bridge`(7), `transform`(6), `trace`(9), `canvas`(4), `mojo-ipc`(5), `skia-capture`(3), 及其他基础设施域

- [ ] jshookmcp 域覆盖矩阵文档（哪些被 agentcloak 替代、哪些保留、哪些部分覆盖）
- [ ] CDP 共享协调优化（agentcloak 7b debugger 域占用 CDP Debugger 时避免冲突）
- [ ] Skill 文件 jshook 协调指导更新

#### 9c: 需求驱动（按需启动）

- [ ] Python SDK — 高层 public API 封装 DaemonClient（有外部 Python 用户要集成时再做，底座 DaemonClient 已就绪）
- [ ] Agent 框架 adapter — LangChain / CrewAI / browser-use 集成（有集成需求时再做，SDK 是前置）
- [ ] Camoufox Firefox 后端（CloakBrowser 被特定 WAF 封杀、需要引擎多样性时再做，ABC 已预留接口）
- [ ] PlaywrightContext 瘦身（做 Camoufox 时自然驱动第二次 base 提取）

---

## Key Design Decisions

### Page Addressing (D9)

`selector_map` + dual mode: numeric index `[N]` primary, coordinate `(x, y)` fallback. Link elements include `href` attribute in snapshot output.

### Batch Invocation (D10)

`--calls-file batch.json --sleep 0.15`。每个 action 前后检测 URL/focus 变化，变化则中止剩余 action 返回 partial results。支持 `$N.path` 结果引用。

### Triple-Surface Architecture (D11, D17, D21)

Skill + CLI（主推，~300 tokens）> MCP（兼容选项，~6000 tokens）> jshook 松耦合（逆向场景）。
MCP token 开销是 CLI 的 20 倍，Bash-capable agent 推荐 CLI 模式。
v0.2.0 起三个表面共享同一个 OpenAPI spec 作为唯一事实来源。

### HybridSession (D12)

Browser ↔ httpx mode switching with automatic cookie + UA + header sync.
httpcloak 提供 TLS 指纹伪装，让 httpx 请求的 TLS 握手和 CloakBrowser 一致。

### Remote Bridge via Chrome Extension (D14)

Zero-setup Chrome extension on Windows, auto-connect back to Linux daemon via WebSocket.

### Captcha Solver Strategy (D19)

Three-tier: Cloudflare Turnstile (screenX patch) → Slider (CV + trajectory) → hCaptcha (physical mouse only).

### API-First Spell Strategy (D22)

参考 neo 的双通道设计。Spell 优先用 captured API 直接调用（快、稳定、不需要浏览器），只在 API 不可用或 credentials 过期时才开浏览器。capture → analyze → generate → run 形成闭环。

### Reverse Engineering Capability Boundary (D23, new)

**agentcloak 吸收网页逆向，jshookmcp 退守非浏览器逆向。**

agentcloak 内建（CDP 原生可达）：debugger（断点/调用栈/scope）、sourcemap（发现/解析/源码树重建）、streaming（WebSocket+SSE 监控）、hooks（init script 注入）、antidebug（反反调试）、network route 拦截、header 注入、GraphQL introspection。

留给外部工具：Proxy（→ mitmproxy CLI）、Transform/AST 反混淆（→ debugger+sourcemap+LLM 组合，极端场景 npx webcrack）、Network 底层（→ dig/scapy/tshark）、二进制逆向/Frida/Ghidra/TLS key log/系统调用/移动端（→ jshookmcp）。

决策依据：jshookmcp 409 tools（35 域）配合使用体验差（双 MCP context 开销、功能重叠、协调成本高）。agentcloak Skill+CLI 模式 ~300 tokens，加逆向能力不显著增加开销。CDP 原生能力不需要额外依赖，保持纯 Python 架构。
调研记录：`.trellis/tasks/05-20-05-20-core-capabilities/research/core-capabilities-survey.md`

---

## Research References

- SOP documents: `reference/doc/sop/` (11 files, CDP/Captcha/Vision/jshookmcp patterns)
- Full research notes: `.trellis/tasks/archive/2026-05/05-06-reference-projects-research/research/`
- v0.2.0 架构决策: `spec/guides/architecture-decisions.md` (D1-D5)
- neo 参考分析: `research/neo-analyzer-wireshark.md` (API-first, schema synthesis, workflow discover)
