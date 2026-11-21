<!-- mcp-name: io.github.7Knife/basic-memory-lifecycle-fork -->
> This is an independent fork of Basic Memory, licensed under AGPL-3.0.

# Basic Memory — Lifecycle Fork

This repository is an independent, experimental fork of
[Basic Memory](https://github.com/basicmachines-co/basic-memory), licensed under
AGPL-3.0. The original project and its copyright belong to Basic Machines, Inc.
This fork is not affiliated with, endorsed by, or maintained by Basic Machines, Inc.

This fork is not a hosted service and does not offer Basic Memory Cloud or any
commercial subscription. It is source code you install and run locally.

## What's different in this fork

- Generic, opt-in memory **lifecycle management**: weight decay, hit bonus,
  canon status, and a review queue.
- New MCP tools:
  - `bm_memory_review_hit` records an explicit retrieval hit after a search hit is
    actually consulted. `bm_search` remains read-only and never writes automatically.
  - `bm_memory_review_propose`, `bm_memory_review_list`,
    `bm_memory_review_decide`, `bm_memory_review_apply`, and
    `bm_memory_review_status` manage a review queue with an explicit
    propose → decide → apply gate.
- Lifecycle features are gated behind `BASIC_MEMORY_LIFECYCLE=1`; upstream
  behavior is unchanged when disabled.

Upstream project: https://github.com/basicmachines-co/basic-memory

## Local install

This fork requires Python 3.12+ and uses [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install "basic-memory @ git+https://github.com/7Knife/basic-memory-lifecycle-fork.git" --prerelease=allow
```

`--prerelease=allow` is required because this code line depends on a FastMCP 4
pre-release; without it, `uv` may silently install an older transitive version.

To enable lifecycle tools and hit recording:

```bash
export BASIC_MEMORY_LIFECYCLE=1
```

On Windows PowerShell:

```powershell
$env:BASIC_MEMORY_LIFECYCLE = "1"
```

## Connect an MCP client

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "uvx",
      "args": [
        "--prerelease=allow",
        "basic-memory @ git+https://github.com/7Knife/basic-memory-lifecycle-fork.git",
        "mcp"
      ],
      "env": {
        "BASIC_MEMORY_LIFECYCLE": "1"
      }
    }
  }
}
```

Restart Claude Desktop. Notes live in `~/basic-memory` by default.

### Claude Code

```bash
claude mcp add basic-memory -- \
  uvx --prerelease=allow \
  "basic-memory @ git+https://github.com/7Knife/basic-memory-lifecycle-fork.git" \
  mcp
```

Set `BASIC_MEMORY_LIFECYCLE=1` in the environment where the MCP server runs if
you want the lifecycle tools enabled.

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.basic-memory]
command = "uvx"
args = [
  "--prerelease=allow",
  "basic-memory @ git+https://github.com/7Knife/basic-memory-lifecycle-fork.git",
  "mcp"
]
env = { BASIC_MEMORY_LIFECYCLE = "1" }
```

### Cursor

Add to `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "uvx",
      "args": [
        "--prerelease=allow",
        "basic-memory @ git+https://github.com/7Knife/basic-memory-lifecycle-fork.git",
        "mcp"
      ],
      "env": {
        "BASIC_MEMORY_LIFECYCLE": "1"
      }
    }
  }
}
```

### VS Code

Add to your User Settings (JSON):

```json
{
  "mcp": {
    "servers": {
      "basic-memory": {
        "command": "uvx",
        "args": [
          "--prerelease=allow",
          "basic-memory @ git+https://github.com/7Knife/basic-memory-lifecycle-fork.git",
          "mcp"
        ],
        "env": {
          "BASIC_MEMORY_LIFECYCLE": "1"
        }
      }
    }
  }
}
```

## Lifecycle behavior

When enabled, this fork adds lifecycle metadata to notes and exposes the
review/hit tools listed above. Lifecycle writes are explicit:

- `bm_search` stays read-only and does not record hits on its own.
- `bm_memory_review_hit` is the explicit write tool for retrieval hits.
- `bm_memory_review_propose/list/decide/status` operate on a review queue;
  `bm_memory_review_apply` returns the approved metadata patch for the standard
  note-write pipeline rather than silently editing notes.

If `BASIC_MEMORY_LIFECYCLE` is not set to `1`, these tools return a structured
`LIFECYCLE_DISABLED` response and the rest of the upstream behavior is unchanged.

## Development

```bash
just install          # Install with dev dependencies
just test-sqlite      # All tests, SQLite
just fast-check       # fix/format/typecheck + impacted tests
just doctor           # File <-> DB consistency check (temp config)
```

Fork-specific core test commands:

```bash
.venv/Scripts/python.exe -m pytest tests/mcp/test_lifecycle_hit.py tests/mcp/test_tool_contracts.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/lifecycle/ tests/mcp/test_tool_bm_search.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/mcp/test_review_server.py -q -p no:cacheprovider
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for fork-specific contribution notes.

## License

[AGPL-3.0](LICENSE). This fork preserves the upstream license and copyright
notices.
