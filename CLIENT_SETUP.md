# Adding Dash MCP Server to Claude Code and Codex CLI

This guide provides exact, up-to-date steps for configuring the Dash MCP Server with Claude Code CLI and Codex CLI.

---

## **Claude Code CLI**

### Quick Method (Recommended)

```bash
claude mcp add --transport stdio dash-api -- uvx --from "git+https://github.com/Kapeli/dash-mcp-server.git" "dash-mcp-server"
```

### With Scope Options

**Local scope** (default - only in current project):
```bash
claude mcp add --transport stdio dash-api -- uvx --from "git+https://github.com/Kapeli/dash-mcp-server.git" "dash-mcp-server"
```

**User scope** (available across all projects):
```bash
claude mcp add --transport stdio dash-api --scope user -- uvx --from "git+https://github.com/Kapeli/dash-mcp-server.git" "dash-mcp-server"
```

**Project scope** (shared with team via `.mcp.json`):
```bash
claude mcp add --transport stdio dash-api --scope project -- uvx --from "git+https://github.com/Kapeli/dash-mcp-server.git" "dash-mcp-server"
```

### Verification

```bash
claude mcp list
```

Or inside Claude Code, type:
```bash
/mcp
```

### Alternative: Direct Config File Edit

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

Then restart Claude Code.

---

## **Codex CLI**

### Method 1: Using CLI Command

```bash
codex mcp add dash-api -- uvx --from "git+https://github.com/Kapeli/dash-mcp-server.git" "dash-mcp-server"
```

### Method 2: Edit Config File Directly (Recommended)

Edit `~/.codex/config.toml`:

```toml
[mcp_servers.dash-api]
command = "uvx"
args = ["--from", "git+https://github.com/Kapeli/dash-mcp-server.git", "dash-mcp-server"]
```

**Important:**
- Use `mcp_servers` (with underscore), NOT `mcp-servers` or `mcpservers`
- Using the wrong name will cause Codex to silently ignore the configuration

### With Environment Variables (if needed)

```toml
[mcp_servers.dash-api]
command = "uvx"
args = ["--from", "git+https://github.com/Kapeli/dash-mcp-server.git", "dash-mcp-server"]
env = { CUSTOM_VAR = "value" }
```

### From IDE Extension

1. Click the gear icon in top right corner
2. Click **Codex Settings > Open config.toml**
3. Add the `[mcp_servers.dash-api]` section shown above
4. Save the file

### Verification

Restart Codex and check that the server is loaded. The MCP server should be available in your next session.

---

## **Key Differences**

| Feature | Claude Code | Codex |
|---------|-------------|-------|
| **Config file** | `~/Library/Application Support/Claude/claude_desktop_config.json` | `~/.codex/config.toml` |
| **Quick add** | `claude mcp add` | `codex mcp add` |
| **Scopes** | local, project, user | N/A (all user-level) |
| **Transport types** | stdio, http, sse | stdio only |
| **Shared config** | No (separate for Desktop/Code) | Yes (CLI + IDE extension) |

---

## **Testing Your Setup**

After installation, you can test the new `fetch_documentation_content` tool:

1. **Search for something:**
   ```
   Search for URLSessionDownloadTask in Dash
   ```

2. **Fetch full documentation:**
   ```
   Get the full documentation for URLSessionDownloadTask
   ```

The AI should use `search_documentation` to find it, then `fetch_documentation_content` to retrieve the complete Markdown content!

---

## **Available Tools**

The Dash MCP Server provides the following tools:

### 1. `list_installed_docsets`
Lists all documentation sets installed in Dash with their identifiers and metadata.

### 2. `search_documentation`
Searches across specified docsets for documentation entries. Returns metadata including:
- Name and type (Class, Function, Method, etc.)
- Platform and docset information
- `load_url` for fetching full content

### 3. `fetch_documentation_content` (New!)
Fetches the complete documentation page and converts it to Markdown format. This provides:
- Full descriptions and overviews
- Parameter details
- Code examples
- Related topics
- Complete API reference

**No content limits** - returns the full documentation for the AI to read and understand.

### 4. `enable_docset_fts`
Enables full-text search indexing for a specific docset to improve search results.

---

## **Troubleshooting**

### Dash Not Found
- Ensure Dash 8+ is installed from https://kapeli.com/dash
- The MCP server will attempt to launch Dash automatically if not running

### API Server Not Enabled
- The server will attempt to enable the Dash API server automatically
- Manual enable: Dash Settings > Integration > Enable API Server
- Or run: `defaults write com.kapeli.dashdoc DHAPIServerEnabled YES`

### Connection Issues
Use the verification commands above to check server status. For Claude Code, `/mcp` will show connection status for each configured server.
