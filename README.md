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
- Search across docsets and code snippets, by docset name or by identifier
- Load documentation pages from search results
- Enable full-text search for specific docsets

### Notice

This is a work in progress. Any suggestions are welcome!

## Tools

1. **list_installed_docsets**
   - Lists all installed documentation sets in Dash
   - Optional `filter`: a case-insensitive substring matched against each docset's name,
     platform and identifier, so `filter="python"` finds the Python docsets without
     fetching all of them. Omit it for the full list. When nothing matches, `suggestions`
     carries the closest names instead — `"sqlachemy"` comes back with SQLAlchemy
2. **search_documentation_by_filter**
   - Searches the docsets matching a name you already know — `"python"`, `"rust"`,
     `"postgres"` — so no identifier lookup is needed first. Usually the one you want
   - `searched_docsets` names what the filter resolved to, so a filter that matched more
     or fewer docsets than intended is visible rather than silent. A filter that matches
     nothing searches nothing and says what was close, rather than guessing
3. **search_documentation**
   - Searches across docsets and snippets, given explicit docset identifiers
4. **load_documentation_page**
   - Loads a documentation page from a `load_url` returned by either search tool
5. **enable_docset_fts**
   - Enables full-text search for a specific docset

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
