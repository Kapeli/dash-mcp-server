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

### Notice

This is a work in progress. Any suggestions are welcome!

## Tools

1. **list_installed_docsets**
   - Lists all installed documentation sets in Dash
2. **search_documentation**
   - Searches across docsets and snippets
3. **load_documentation_page**
   - Loads a documentation page from a `load_url` returned by `search_documentation`
4. **enable_docset_fts**
   - Enables full-text search for a specific docset

## Response size

Two environment variables set how much a tool is allowed to return. Both take a token
count; **`0` means no limit**, and leaving a variable unset keeps the default.

| Variable | Default | Applies to |
| --- | --- | --- |
| `DASH_RESPONSE_TOKEN_LIMIT` | `25000` | `list_installed_docsets`, `search_documentation` |
| `DASH_RETRIEVAL_TOKEN_LIMIT` | `0` (no limit) | `load_documentation_page` |

The split is deliberate. Listing and searching return many small records, and a cap keeps
a broad query from flooding the caller — a search can match hundreds of entries. Loading a
page is the opposite: you asked for one specific document and usually want all of it, so
nothing is trimmed unless you ask for it. Both defaults match how the server behaved
before the variables existed.

`load_documentation_page` also takes `max_tokens` per call, which overrides
`DASH_RETRIEVAL_TOKEN_LIMIT` for that call. A page cut short comes back with `truncated`
set, `next_offset` saying where to resume, and `total_characters` for the whole page — so
a long page can be read in successive calls by passing `next_offset` back as `offset`.

```json
{
  "mcpServers": {
    "dash-api": {
      "command": "uvx",
      "args": ["dash-mcp-server"],
      "env": {
        "DASH_RESPONSE_TOKEN_LIMIT": "25000",
        "DASH_RETRIEVAL_TOKEN_LIMIT": "50000"
      }
    }
  }
}
```

An unparseable or blank value falls back to the default rather than failing the server at
startup, and a negative one reads as no limit.

The tools describe whatever you set. Each tool's description carries its own limit rather
than a fixed number, so a model planning a call sees the budget it will actually get — and
when there is no limit the sentence is dropped rather than reworded:

```
DASH_RESPONSE_TOKEN_LIMIT unset or 25000
  list_installed_docsets   ... Results are truncated if they would exceed 25,000 tokens,
                               set by DASH_RESPONSE_TOKEN_LIMIT.

DASH_RESPONSE_TOKEN_LIMIT=4000
  list_installed_docsets   ... Results are truncated if they would exceed 4,000 tokens,
                               set by DASH_RESPONSE_TOKEN_LIMIT.

DASH_RESPONSE_TOKEN_LIMIT=0
  list_installed_docsets   ... (no truncation sentence at all)
```

Descriptions are built when the server registers its tools, so changing a variable changes
what the model is told on the next restart.

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
