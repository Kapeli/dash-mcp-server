# mcp-server-dash

A Model Context Protocol (MCP) server that provides tools to interact with the [Dash](https://kapeli.com/dash) documentation browser API.

Dash 8 is required, which is currently in beta. You can download Dash 8 at https://blog.kapeli.com/dash-8.

<a href="https://glama.ai/mcp/servers/@Kapeli/dash-mcp-server">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/@Kapeli/dash-mcp-server/badge" alt="Dash Server MCP server" />
</a>

## Overview

The Dash MCP server provides tools for accessing and searching documentation directly from Dash, the macOS documentation browser. MCP clients can:

- List installed docsets
- Search across docsets and code snippets
- Enable full-text search for specific docsets

### Notice

This is a work in progress. Any suggestions are welcome!

## Tools

1. **list_installed_docsets**
   - Lists all installed documentation sets in Dash
2. **search_documentation**
   - Searches across docsets and snippets
3. **enable_docset_fts**
   - Enables full-text search for a specific docset

## Requirements

- macOS (required for Dash app)
- [Dash](https://kapeli.com/dash) installed
- Python 3.11.4 or higher
- uv

## Configuration

### Using uvx

```bash
brew install uv
```

#### in `claude_desktop_config.json`

```json
{
  "mcpServers": {
      "dash-api": {
          "command": "uvx",
          "args": [
              "--from",
              "git+https://github.com/Kapeli/dash-mcp-server.git",
              "dash-mcp-server"
          ]
      }
  }
}
```

#### in `Claude Code`

```bash
claude mcp add dash-api -- uvx --from "git+https://github.com/Kapeli/dash-mcp-server.git" "dash-mcp-server"
```

#### Running directly

```bash
uv sync
```

```json
{
  "mcpServers": {
    "dash-api": {
      "command": "/opt/homebrew/bin/uv",
      "args": [
        "--directory",
        "/Users/guochunzhong/git/oss/dash-mcp-server",
        "run",
        "src/dash_mcp_server/server.py"
      ]
    }
  }
}
```
