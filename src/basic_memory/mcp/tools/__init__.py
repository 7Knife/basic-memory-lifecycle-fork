"""MCP tools for Basic Memory.

This package provides the complete set of tools for interacting with
Basic Memory through the MCP protocol. Importing this module registers
all tools with the MCP server.
"""

# 1.0 tool surface (SPEC-35.2, #1827).
from basic_memory.mcp.tools.context import bm_context
from basic_memory.mcp.tools.delete import bm_delete
from basic_memory.mcp.tools.edit import bm_edit
from basic_memory.mcp.tools.find_files import bm_find
from basic_memory.mcp.tools.listing import bm_list
from basic_memory.mcp.tools.lifecycle_decay import bm_memory_decay
from basic_memory.mcp.tools.lifecycle_hit import bm_memory_review_hit
from basic_memory.mcp.tools.lifecycle_review import (
    bm_memory_review_apply,
    bm_memory_review_decide,
    bm_memory_review_list,
    bm_memory_review_propose,
    bm_memory_review_status,
    register_review_tools,
)
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.manual import bm_man
from basic_memory.mcp.tools.move import bm_move
from basic_memory.mcp.tools.note_schemas import (
    bm_schema_diff,
    bm_schema_infer,
    bm_schema_validate,
)

from basic_memory.mcp.tools.projects import (
    bm_project_create,
    bm_project_delete,
    bm_project_list,
    bm_recent,
)
from basic_memory.mcp.tools.ranked_search import bm_search
from basic_memory.mcp.tools.read import bm_read
from basic_memory.mcp.tools.write import bm_write

__all__ = [
    "bm_context",
    "bm_delete",
    "bm_edit",
    "bm_find",
    "bm_list",
    "bm_man",
    "bm_memory_decay",
    "bm_memory_review_apply",
    "bm_memory_review_decide",
    "bm_memory_review_hit",
    "bm_memory_review_list",
    "bm_memory_review_propose",
    "bm_memory_review_status",
    "bm_move",
    "bm_project_create",
    "bm_project_delete",
    "bm_project_list",
    "bm_read",
    "bm_recent",
    "bm_schema_diff",
    "bm_schema_infer",
    "bm_schema_validate",
    "bm_search",
    "bm_write",
]

# Register the lifecycle review tools on the main server. The tools are plain
# functions; registration is explicit so the same surface also mounts on the
# standalone review server without duplicating definitions.
register_review_tools(mcp)
