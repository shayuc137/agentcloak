# MCP setup

agentcloak provides an MCP (Model Context Protocol) server with 39 tools for AI clients that support native tool discovery.

## Skill + CLI vs MCP

agentcloak offers two integration modes. For agents that can run shell commands (most can), the Skill + CLI mode is recommended.

| | Skill + CLI (recommended) | MCP Server |
|---|---|---|
| **How it works** | Agent calls `cloak` via Bash | `agentcloak-mcp` runs as MCP server |
| **Context cost** | ~300 tokens (loaded on demand) | ~6,000 tokens (persistent in context) |
| **Setup** | Copy one Skill file | One config line |
| **Best for** | Claude Code, any Bash-capable agent | Pure MCP clients without Bash access |

> [!TIP]
> The MCP server exposes the same capabilities as the CLI -- they share the same daemon backend. The only difference is how the agent discovers and invokes commands. CLI mode uses 20x less context.

## Skill + CLI setup (recommended)

The Skill bundle (`SKILL.md` + `references/`) teaches your agent how to use `cloak` commands. It auto-loads when the agent needs browser capabilities. See the full [Skill installation guide](../getting-started/installation.md#install-the-skill-bundle) for per-platform paths; the example below targets Claude Code (project-scoped):

```bash
# 1. Install the CLI + daemon + stealth browser
pip install agentcloak

# 2. Install the Skill bundle (SKILL.md + references/)
mkdir -p .claude/skills
curl -L https://github.com/shayuc137/agentcloak/archive/refs/heads/main.tar.gz \
  | tar -xz --strip-components=2 -C .claude/skills \
    agentcloak-main/skills/agentcloak
```

After this, Claude Code automatically picks up the Skill when a task involves web pages. No further configuration needed.

## MCP server setup

The MCP server is included in the base install (`pip install agentcloak`).

The MCP server command is `agentcloak-mcp`. It uses stdio transport and auto-starts the daemon on the first request.

### Claude Code

One command, no file editing needed:

```bash
claude mcp add agentcloak -- agentcloak-mcp
```

### Codex

Add to `.codex/mcp.json` in your project root:

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

Add to Cursor Settings > MCP Servers, or create `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "agentcloak": {
      "command": "agentcloak-mcp"
    }
  }
}
```

### Other MCP clients

Use the same JSON pattern. The MCP server command is `agentcloak-mcp` (stdio transport, no additional arguments needed).

### With uvx (no install needed)

Run the MCP server on-the-fly without installing agentcloak globally:

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

## Available MCP tools

The MCP server exposes 39 tools. See the [MCP reference](../reference/mcp.md) for the full list with parameters.

| Category | Tools |
|----------|-------|
| Navigation | `agentcloak_navigate`, `agentcloak_snapshot`, `agentcloak_screenshot` |
| Interaction | `agentcloak_action` |
| Content | `agentcloak_evaluate`, `agentcloak_fetch` |
| Network | `agentcloak_network` |
| Capture | `agentcloak_capture_control`, `agentcloak_capture_query` |
| Dialog | `agentcloak_dialog` |
| Wait | `agentcloak_wait` |
| Upload | `agentcloak_upload` |
| Frame | `agentcloak_frame` |
| Management | `agentcloak_status`, `agentcloak_launch`, `agentcloak_tab`, `agentcloak_profile`, `agentcloak_doctor`, `agentcloak_resume` |
| Cookies | `agentcloak_cookies` |
| Page hiding | `agentcloak_hide` |
| Spells | `agentcloak_spell_run`, `agentcloak_spell_list` |
| Bridge | `agentcloak_bridge` |
| Reverse engineering | `agentcloak_script`, `agentcloak_route`, `agentcloak_headers`, `agentcloak_graphql`, `agentcloak_streaming`, `agentcloak_debugger`, `agentcloak_sourcemap` |

## Verifying MCP setup

After configuring, test the connection:

1. Ask your agent to "check browser status" -- it should call `agentcloak_status`
2. Ask it to "navigate to example.com" -- it should call `agentcloak_navigate`
3. Ask it to "take a snapshot" -- it should call `agentcloak_snapshot`

If the daemon isn't running, the MCP server auto-starts it on the first request.

## Troubleshooting

**MCP server not found**: Make sure `agentcloak-mcp` is on your PATH. Run `which agentcloak-mcp` to verify.

**Daemon connection failed**: The MCP server auto-starts the daemon. Check `cloak doctor` for diagnostics.

**Tools not appearing**: Restart your AI client after adding MCP configuration. Some clients cache tool definitions.
