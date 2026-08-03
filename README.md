# mcp-server-dash

A Model Context Protocol (MCP) server that provides tools to interact with the [Dash](https://kapeli.com/dash) documentation browser API.

Dash 8 is required. You can download Dash 8 at https://blog.kapeli.com/dash-8.

mcp-name: io.github.Kapeli/dash-mcp-server

<a href="https://glama.ai/mcp/servers/@Kapeli/dash-mcp-server">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/@Kapeli/dash-mcp-server/badge" alt="Dash Server MCP server" />
</a>

## Overview

The Dash MCP server provides tools for accessing and searching documentation directly from Dash, the macOS documentation browser. MCP clients can:

- List installed docsets
- Search across docsets and code snippets
- Load documentation pages from search results
- Enable full-text search for specific docsets
- Explicitly launch Dash and enable the Dash API server when setup is needed

### Notice

This is a work in progress. Any suggestions are welcome!

## Tools

1. **ensure_dash_api_ready**
   - Explicitly prepares Dash for MCP access by optionally launching Dash and enabling the API server
   - This is the only setup tool that may launch Dash or write Dash macOS preferences
2. **list_installed_docsets**
   - Lists all installed documentation sets in Dash
   - Read-only; does not launch Dash or change Dash settings
3. **search_documentation**
   - Searches across docsets and snippets
   - Accepts `docset_identifiers` as a list of identifiers returned by `list_installed_docsets`
   - Read-only; does not launch Dash or change Dash settings
4. **load_documentation_page**
   - Loads a documentation page from a `load_url` returned by `search_documentation`
   - Only accepts local Dash API URLs on the active Dash API port
5. **enable_docset_fts**
   - Enables full-text search for a specific docset
   - Returns a structured result with `success`, `identifier`, and optional `error`

## Requirements

- macOS (required for Dash app)
- [Dash](https://kapeli.com/dash) installed
- Python 3.12 or higher
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
