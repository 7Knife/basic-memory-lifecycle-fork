"""Standalone MCP server for the lifecycle review tools.

Mount the review tools on their own FastMCP server without pulling in the
full Basic Memory stack (no database, watchers, or project containers).
This is the entry point for hosts that want lifecycle review as a separate
MCP connection:

    BASIC_MEMORY_LIFECYCLE=1 python -m basic_memory.mcp.review_server

or, as a stdio MCP server configured in a client:

    {
      "mcpServers": {
        "basic-memory-review": {
          "command": "uv",
          "args": ["run", "--with", "basic-memory", "python", "-m",
                   "basic_memory.mcp.review_server"],
          "env": {"BASIC_MEMORY_LIFECYCLE": "1"}
        }
      }
    }

The review tools operate on the JSON proposal queue only; applying a
proposal's metadata patch goes through the main Basic Memory server's
`bm_edit(command='set_metadata')` to keep the standard prepare -> persist
write path.
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

from basic_memory.mcp.tools.lifecycle_review import register_review_tools


def build_review_server() -> FastMCP:
    """Create a FastMCP server carrying only the lifecycle review tools."""
    server = FastMCP(
        name="Basic Memory Review",
        instructions=(
            "Lifecycle review server for Basic Memory. Queue lifecycle change "
            "proposals (new/update/status_progression/archive), list pending "
            "proposals, decide them, and fetch the approved metadata patch to "
            "apply on the main Basic Memory server with "
            "bm_edit(command='set_metadata'). Requires BASIC_MEMORY_LIFECYCLE=1."
        ),
    )
    register_review_tools(server)
    return server


def main() -> None:
    # Transport defaults to stdio, the standard for MCP client configurations.
    server = build_review_server()
    if os.environ.get("BASIC_MEMORY_LIFECYCLE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        # Trigger: lifecycle review is opt-in.
        # Why: the tools would otherwise silently return LIFECYCLE_DISABLED to
        # every client, which looks like a broken server rather than a config gap.
        # Outcome: fail fast at startup with the exact env var to set.
        raise SystemExit(
            "BASIC_MEMORY_LIFECYCLE must be enabled (BASIC_MEMORY_LIFECYCLE=1) "
            "to run the review server."
        )
    server.run()


if __name__ == "__main__":
    main()
