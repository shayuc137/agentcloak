# Snapshot 模型

agentcloak 将网页表示为带缩进的无障碍树，每个可交互元素拥有稳定的 `[N]` 引用。agent 通过这些数字操作元素，而不是用 CSS 选择器。本页讲清楚这棵树从哪里来、剪掉了什么、以及渐进加载如何让超大页面也能放进 agent 的 context 窗口。

## 从 Chrome AX tree 到缩进文本

当你执行 `cloak snapshot` 时，daemon 通过 CDP（`Accessibility.getFullAXTree`）从 Chromium 拉取完整的无障碍树，然后渲染成带缩进的文本。CLI 会在树前面加一行 header，包含标题/URL/节点数/`seq`，agent 一眼就能定位：

```text
# Shop home | https://shop.example/ | 23 nodes (5 interactive) | seq=4
navigation "Main Nav"
  [1] link "Home"
  [2] link "Shop"
  [3] textbox "Search" value="shoes" focused
main "Content"
  [4] link "Item 1 - $29.99" href="/items/1"
  [5] button "Add to cart"
```

每一行树正文是 `<缩进><role> "<name>" <属性>`。可交互节点按文档顺序分配 `[N]` 编号；这个编号就是 action 命令的目标参数（位置参数 `cloak click 5` 或 `--index 5`），只在页面状态未变时有效。

共享构建器位于 `src/agentcloak/browser/_snapshot_builder.py`——`CloakBrowser` 和 `RemoteBridge` 两个后端都用同一份 CDP 节点输入调用它，所以无论用哪个后端，树的格式完全一致。

## Snapshot 模式

`cloak snapshot --mode <mode>` 选择不同的表示形式：

| 模式 | 输出内容 | 何时使用 |
|------|---------|---------|
| `compact`（默认） | 仅交互节点 + landmark；折叠 `generic`/`group` | token 紧张的交互循环 |
| `accessible` | 完整 a11y 树，包含全部 `[N]` 引用 | 首次观察、复杂布局 |
| `content` | 纯可见文本，无 role 无引用 | 文章抽取、内容摘要 |
| `dom` | 原始 outer HTML | ARIA 隐藏了需要的信息时（罕见） |

`compact` 是 v0.2.0 以后的默认——agent 几乎总是只需要交互元素和结构 landmark（`navigation`、`main`、`form`、`dialog`），不需要匿名 `<div>` 包装层。只在默认输出缺了上下文时才退到 `accessible`。

## ARIA 状态提取

输入框和开关元素的实时状态直接显示在树里：

```
[3] textbox "Search" value="shoes" focused
[7] checkbox "Remember me" checked
[8] button "Submit" disabled
[12] combobox "Country" expanded haspopup=listbox
[15] slider "Volume" valuenow=70 valuemin=0 valuemax=100
```

构建器会提取这些布尔 ARIA 属性：`checked`、`disabled`、`expanded`、`selected`、`pressed`、`invalid`、`required`、`focused`、`hidden`。值属性（`value`、`valuetext`、`valuemin`、`valuemax`、`valuenow`、`level`、`haspopup`、`autocomplete`）也在出现时显示。

密码字段通过 `autocomplete="current-password"` / `new-password` 自动识别，渲染为 `value="••••"`——agent 可以确认输入成功，但不会泄露真实密码。

## 链接 href 抽取

链接节点直接在树里附带 `href`，agent 无需额外的 `evaluate()` 调用就能拿到跳转目标：

```
[4] link "Documentation" href="/docs/"
[5] link "GitHub" href="https://github.com/cloak-hq/agentcloak"
```

## 渐进加载

大页面可能产生上千节点。daemon 会缓存每次完整 snapshot，并提供三个参数对缓存做切片，无需再次访问浏览器：

| 参数 | 效果 |
|------|------|
| `--limit 80`（旧别名 `--max-nodes`） | 截断为 N 行可见输出，附上 `[+ 412 more nodes]` 汇总 |
| `--focus N` | 仅打印以 `[N]` 为根的子树，附加祖先面包屑 |
| `--offset 80` | 从第 N 个元素开始分页（继续 `--limit` 截断之后的内容） |

即使某个 `[N]` 引用被截断了不在可见输出里，对它的 action 依然有效——selector_map 在整个缓存 snapshot 上始终生效。这是探索大页面的推荐模式：

```bash
cloak snapshot --limit 80                    # 概览
cloak snapshot --focus 42                    # 钻进感兴趣的部分
cloak click 42                               # 操作
```

当目标区域有稳定的 CSS 选择器时，可用 `cloak snapshot --within main` 在分配引用前裁剪原始无障碍树。生成的树和 selector map 只包含主文档中的该子树，从而避开导航栏、侧栏和工具栏。选择器范围不能与 `--frames` 或 `--mode dom` 组合使用。

## 多 frame snapshot

默认 snapshot 只覆盖当前 frame。加上 `--frames` 后，daemon 会把子 iframe 的 AX 树合并进父树——遍历每个 frame 并把子树嵌入到对应的 iframe 节点下。用于支付控件、嵌入表单、OAuth 对话框这类住在 iframe 里的场景。

## Diff 模式

`cloak snapshot --diff` 会和上一次模式、选择器及 frame 设置均相同的缓存 snapshot 比较，给每行打标记：

```
  [3] textbox "Search" value="shoes" focused
[+] [9] button "Apply filter"           # 新增
[~] [4] link "Cart (2)" href="/cart"    # 文本或属性变化
```

删除的行在末尾汇总成一段。diff 是纯按行级比较，不追踪 `[N]` 编号重排——用于"页面发生了什么变化"的感知，不能当完整变更日志。

## selector_map 与 backend_node_map

内部 snapshot 在树文本旁还携带两份映射：

- `selector_map`：`{N: ElementRef(index, tag, role, text, attributes, depth)}`——action 通过 positional `N`（如 `cloak click 5`）或 `--index N` 在这里解析元素
- `backend_node_map`：`{N: CDP backendNodeId}`——RemoteBridge 后端通过 CDP 命令操作元素时用的持久 Chromium 节点 ID

CLI 文本输出默认不包含 selector_map（agent 从树正文里就能读到 `[N]` 引用）。临时需要可加 `--selector-map`，或用 `--json` 模式从 `data.selector_map` 字段读取。MCP 工具也默认不返回。backend_node_map 是内部数据——agent 不需要直接接触。

## Token 经济性

中等复杂页面（Hacker News 首页）下的大致估算：

| 模式 | 行数 | 大约 token |
|------|------|----------|
| `accessible`（完整） | 800-1500 | 4000-8000 |
| `compact`（默认） | 80-200 | 400-1200 |
| `compact --limit 80` | 80 + 汇总 | ~500 |
| `content` | 50-100 | 200-600 |

交互循环中默认 `compact` + `--limit 80` 就是甜蜜点，只在页面确实需要时再放宽窗口。文本抽取（文章、搜索结果）用 `--mode content` 比解析完整树便宜得多。
