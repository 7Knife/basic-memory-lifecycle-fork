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
  - `bm_memory_decay` applies one weight-decay step to a note: a note unused for
    long enough loses weight, and a note whose weight falls under the archive
    threshold is archived.
  - `bm_memory_review_propose`, `bm_memory_review_list`,
    `bm_memory_review_decide`, `bm_memory_review_apply`, and
    `bm_memory_review_status` manage a review queue with an explicit
    propose → decide → apply gate. Each proposal belongs to one project, and
    a project only sees and decides its own proposals.
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
review/hit/decay tools listed above. Lifecycle writes are explicit:

- `bm_search` stays read-only and does not record hits on its own.
- `bm_memory_review_hit` is the explicit write tool for retrieval hits.
- `bm_memory_decay` is the explicit write tool for decay. It lowers a note's
  weight by one decay step and archives the note when the weight falls under
  the archive threshold. A note with no elapsed interval, or one already
  archived, is reported as unchanged and nothing is written. `force=True`
  applies a single day's decay regardless of the interval.
- `bm_memory_review_propose/list/decide/status` operate on a review queue;
  `bm_memory_review_apply` returns the approved metadata patch for the standard
  note-write pipeline rather than silently editing notes. Proposals belong to
  one project: a project only sees, and only decides, its own proposals, so a
  proposal made in one project cannot be applied to a same-named note in
  another. A proposal queued before this rule existed carries no project and
  is refused until it is proposed again.

Decay is per note, so a note's weight only moves when `bm_memory_decay` is
called for it. It is not run automatically during indexing.

`bm_search` reads the lifecycle fields of every note it returns, so
`sort_by="weight"` ranks by a note's weight and `include_archived=False`
hides archived notes. Those fields are also available to `bm_search`'s
`filters` argument, alongside the rest of a note's frontmatter.

If `BASIC_MEMORY_LIFECYCLE` is not set to `1`, these tools return a structured
`LIFECYCLE_DISABLED` response and the rest of the upstream behavior is unchanged.

### Weight bounds

A note's weight lives between `ARCHIVE_THRESHOLD` (1.0, below which the note is
archived) and `MAX_WEIGHT` (3.0).

- A retrieval hit raises the weight by `HIT_BONUS` (0.5), up to
  `base_weight * HIT_CAP_FACTOR` (1.5). The base weight is written on the
  first hit and stays put, so repeated hits converge instead of raising the
  ceiling they are measured against.
- Decay lowers the weight by `DECAY_PER_DAY` (0.1) per day, charged once per
  `DECAY_INTERVAL_DAYS` (3) since the note's last hit or decay. Days that do
  not fill a whole interval stay on the timeline, so what a note loses depends
  on how long it has been unused, not on how often decay was run.

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
.venv/Scripts/python.exe -m pytest tests/mcp/test_lifecycle_decay.py tests/mcp/test_lifecycle_search.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/lifecycle/ tests/mcp/test_tool_bm_search.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/mcp/test_review_server.py -q -p no:cacheprovider
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for fork-specific contribution notes.

## License

[AGPL-3.0](LICENSE). This fork preserves the upstream license and copyright
notices.
