# MCP 配置

agentcloak 提供了一个 MCP（Model Context Protocol）server，包含 38 个工具，供支持原生工具发现的 AI 客户端使用。

## Skill + CLI 与 MCP 对比

agentcloak 提供两种集成模式。对于能运行 shell 命令的 agent（绝大多数都可以），推荐使用 Skill + CLI 模式。

| | Skill + CLI（推荐） | MCP Server |
|---|---|---|
| **工作方式** | Agent 通过 Bash 调用 `cloak` | `agentcloak-mcp` 作为 MCP server 运行 |
| **上下文开销** | ~300 tokens（按需加载） | ~6,000 tokens（常驻） |
| **配置方式** | 复制一个 Skill 文件 | 一行配置 |
| **适用场景** | Claude Code 及任何支持 Bash 的 agent | 没有 Bash 能力的纯 MCP 客户端 |

> [!TIP]
> MCP server 暴露的能力与 CLI 完全相同 -- 它们共享同一个 daemon 后端。唯一的区别在于 agent 如何发现和调用命令。CLI 模式的上下文占用仅为 MCP 的二十分之一。

## Skill + CLI 配置（推荐）

Skill 安装包（`SKILL.md` + `references/`）教会 agent 如何使用 `cloak` 命令，需要时自动加载。各 agent 平台的安装路径详见[Skill 安装指南](../getting-started/installation.md#安装-skill-安装包)。下面以 Claude Code 项目级目录为例：

```bash
# 1. 安装 CLI + daemon + 隐身浏览器
pip install agentcloak

# 2. 安装 Skill 安装包（SKILL.md + references/）
mkdir -p .claude/skills
curl -L https://github.com/shayuc137/agentcloak/archive/refs/heads/main.tar.gz \
  | tar -xz --strip-components=2 -C .claude/skills \
    agentcloak-main/skills/agentcloak
```

完成后，Claude Code 在涉及网页的任务中会自动使用该 Skill，无需其他配置。

## MCP server 配置

MCP server 已包含在基础安装中（`pip install agentcloak`）。

MCP server 命令为 `agentcloak-mcp`，使用 stdio 传输，在首次请求时自动启动 daemon。

### Claude Code

一行命令，无需编辑文件：

```bash
claude mcp add agentcloak -- agentcloak-mcp
```

### Codex

添加到项目根目录的 `.codex/mcp.json`：

```json
{
  "mcpServers": {
    "agentcloak": {
      "command": "agentcloak-mcp"
    }
  }
}
```

### Cursor

添加到 Cursor 设置 > MCP Servers，或在项目根目录创建 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "agentcloak": {
      "command": "agentcloak-mcp"
    }
  }
}
```

### 其他 MCP 客户端

使用相同的 JSON 格式。MCP server 命令为 `agentcloak-mcp`（stdio 传输，无需额外参数）。

### 使用 uvx（免安装）

通过 uvx 免安装运行 MCP server：

```json
{
  "mcpServers": {
    "agentcloak": {
      "command": "uvx",
      "args": ["agentcloak[mcp]"]
    }
  }
}
```

## 可用 MCP 工具

MCP server 暴露 38 个工具。完整参数列表参见 [MCP 参考](../reference/mcp.md)。

| 分类 | 工具 |
|------|------|
| 导航 | `agentcloak_navigate`、`agentcloak_snapshot`、`agentcloak_screenshot` |
| 交互 | `agentcloak_action` |
| 内容 | `agentcloak_evaluate`、`agentcloak_fetch` |
| 网络 | `agentcloak_network` |
| 捕获 | `agentcloak_capture_control`、`agentcloak_capture_query` |
| 对话框 | `agentcloak_dialog` |
| 等待 | `agentcloak_wait` |
| 上传 | `agentcloak_upload` |
| Frame | `agentcloak_frame` |
| 管理 | `agentcloak_status`、`agentcloak_launch`、`agentcloak_tab`、`agentcloak_profile`、`agentcloak_doctor`、`agentcloak_resume` |
| Cookie | `agentcloak_cookies` |
| Spell | `agentcloak_spell_run`、`agentcloak_spell_list` |
| Bridge | `agentcloak_bridge` |
| 网页逆向 | `agentcloak_script`、`agentcloak_route`、`agentcloak_headers`、`agentcloak_graphql`、`agentcloak_streaming`、`agentcloak_debugger`、`agentcloak_sourcemap` |

## 验证 MCP 配置

配置完成后，测试连接：

1. 让 agent "检查浏览器状态" -- 它应该调用 `agentcloak_status`
2. 让它 "导航到 example.com" -- 它应该调用 `agentcloak_navigate`
3. 让它 "获取 snapshot" -- 它应该调用 `agentcloak_snapshot`

如果 daemon 未运行，MCP server 会在首次请求时自动启动。

## 故障排除

**MCP server 找不到**：确保 `agentcloak-mcp` 在 PATH 中。运行 `which agentcloak-mcp` 验证。

**Daemon 连接失败**：MCP server 会自动启动 daemon。运行 `cloak doctor` 进行诊断。

**工具未显示**：添加 MCP 配置后重启 AI 客户端。部分客户端会缓存工具定义。
