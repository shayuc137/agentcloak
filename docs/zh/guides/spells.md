# Spell

**Spell** 是可复用、有名字的站点操作——每个站点任务一条命令。与其让 agent 反复琢磨"怎么按语言搜 GitHub 仓库"，不如写一个 `github/repos-by-language` spell，然后在任何地方 `cloak spell run github/repos-by-language lang=python`。spell 编码了最低成本的可行传输方式（往往是直接 API 调用），所以比 UI 自动化快几个数量级。

## 快速开始

```bash
cloak spell list                              # 看注册了哪些
cloak spell info httpbin/headers              # 单个 spell 详情
cloak spell run httpbin/headers               # 执行
cloak spell run weather/current city=Tokyo    # 带参的 spell 用 key=value
```

## Strategy

每个 spell 声明一个 `Strategy`，告诉执行器它需要什么：

| Strategy | 行为 | 需要浏览器？ |
|----------|------|-----------|
| `PUBLIC` | 纯 HTTP 调用，无认证 | 不需要 |
| `COOKIE` | 用浏览器 cookie 重放 HTTP | 需要（取 cookie） |
| `HEADER` | 用捕获的认证 header 重放 HTTP | 需要（取 header） |
| `INTERCEPT` | 钩住真实浏览器请求抽取结果 | 需要 |
| `UI` | 通过点击 / 填表驱动页面 | 需要 |

总选能用的最低 strategy。`PUBLIC` 没有浏览器开销；`UI` 是兜底方案，给没有可用 API 的站点。模式 analyzer（`cloak capture analyze`）会告诉你站点支持哪种 strategy。

## 命令

| 命令 | 用途 |
|------|------|
| `cloak spell list` | 列出所有注册 spell，含 site、name、strategy |
| `cloak spell info SITE/NAME` | strategy、参数、domain、描述、源码位置 |
| `cloak spell run SITE/NAME` | 执行（需要时浏览器自动启动） |
| `cloak spell run SITE/NAME k=v k2=v2` | 以 `key=value` 对的形式位置传 `Arg` |
| `cloak spell scaffold SITE` | 从 `cloak capture analyze` 输出生成 spell 桩 |

Spell 名永远是 `site/command`——site 把相关 spell 组在一起，command 标识具体操作。

## 编写 spell

两种模式：pipeline（声明式）和 function（Python 代码）。

### Pipeline 模式

直白的 API 调用，声明一组步骤。内置步骤包括 `fetch`、`select`、`extract`、`transform`，以及 `{args.name}` 风格的模板插值：

```python
from agentcloak.core.types import Strategy
from agentcloak.spells.registry import spell

@spell(
    site="httpbin",
    name="headers",
    strategy=Strategy.PUBLIC,
    description="Inspect request headers via httpbin.org",
    pipeline=[
        {"fetch": {"url": "https://httpbin.org/headers"}},
        {"select": "headers"},
    ],
)
def httpbin_headers() -> None:
    """Pipeline 占位——pipeline spell 不使用函数体。"""
```

设了 `pipeline=` 时，被装饰的函数只是占位；registry 存的是 pipeline，由它执行。

`fetch` 步骤在有 browser 时会继承 session 状态（cookies、`User-Agent`、proxy）；`PUBLIC` 模式没有 browser 时退化为 httpx，但默认 UA 用 CloakBrowser 对齐的 Chrome 字符串，和 stealth session 发出的请求一致。需要覆盖时在 `headers` 里写 `{"User-Agent": "..."}`；按调用变化的话再加 spell `arg`。

### Function 模式

需要分支、多步骤浏览器交互、或动态参数时，写一个异步 handler：

```python
from agentcloak.core.types import Strategy
from agentcloak.spells.context import SpellContext
from agentcloak.spells.registry import spell

@spell(
    site="example",
    name="title",
    strategy=Strategy.COOKIE,
    domain="example.com",
    description="Get the page title of example.com",
)
async def example_title(ctx: SpellContext) -> list[dict[str, object]]:
    title = await ctx.evaluate("document.title")
    url = await ctx.evaluate("location.href")
    return [{"title": title, "url": url}]
```

`SpellContext` 暴露浏览器操作：`ctx.navigate(url)`、`ctx.evaluate(js)`、`ctx.click(target)`、`ctx.fetch(url, ...)`。返回 `list[dict]`——每个 dict 是 spell 输出的一行。

当 `strategy` 是 `COOKIE` / `HEADER` 且设了 `domain` 时，执行器在调 handler 前会自动跳到 `https://<domain>`，确保 cookie/header 已注入。

## 自动发现

daemon 启动时从两个位置自动发现 spell：

1. **内置：**`src/agentcloak/spells/sites/`——随包提供，含 `httpbin` 和 `example` 范例
2. **用户目录：**`~/.config/agentcloak/spells/*.py`——你的；不会被升级覆盖

两个位置下的任意 `.py` 文件都会被 import 一次；文件里的 `@spell(...)` 装饰器在 import 时注册。新增 spell 只需把 Python 文件放进用户目录——Linux 下不强制重启 daemon（`spell list` 每次会重新扫描目录），但重启是最稳的路径。

## Capture → analyze → scaffold

capture 系统喂入 spell 生成。完整流程：

```bash
cloak capture start
# 在浏览器手动执行工作流
cloak capture stop
cloak capture analyze --domain target-site.com
cloak spell scaffold target-site --domain target-site.com
```

`spell scaffold` 为每个端点聚类在 `~/.config/agentcloak/spells/` 下写一个 Python 文件，strategy 已根据捕获的认证推断、URL 模板已按检测到的路径参数填好、每个变量段对应一个 `Arg`。你润色生成的桩、测试、上线。

录制侧详见 [capture 指南](./capture.md)。

## 惯例

- **命名：**`site/verb-noun`——`github/repos-by-language`、`hn/top-stories`、`linear/issue-status`
- **一个站点一个文件**放在 `~/.config/agentcloak/spells/`，文件内可有多个 `@spell`
- **Arg：**用 kebab-case 命名，命令行 `key=value` 解析时不会被破坏
- **输出：**永远是 `list[dict]`，让 JSON envelope 在所有 spell 之间一致
- **副作用：**有破坏性的 spell 在装饰器里标 `access="write"`，调用者可以审计
