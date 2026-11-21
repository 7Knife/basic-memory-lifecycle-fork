"""Generic lifecycle review MCP tools.

These tools manage a JSON-backed proposal queue with an explicit
propose -> decide -> apply gate. Lifecycle is opt-in via BASIC_MEMORY_LIFECYCLE=1.

`bm_memory_review_propose`, `bm_memory_review_list`, `bm_memory_review_decide`
operate on the queue only. `bm_memory_review_apply` returns the approved metadata
patch to apply with `bm_edit(command='set_metadata')`, which uses Basic Memory's
standard note-write pipeline. This keeps the tool independent of project container
internals while preserving the confirmation buffer semantics.

The tools are plain functions registered through `register_review_tools(server)`,
so the same surface mounts on the main Basic Memory server (via the tool
registry) or on the standalone `basic_memory.mcp.review_server` without
duplicating definitions.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from basic_memory.lifecycle import ReviewQueue, lifecycle_enabled
from basic_memory.mcp.tool_params import OutputFormat

REVIEW_TOOL_NAMES = frozenset({
    "bm_memory_review_propose",
    "bm_memory_review_list",
    "bm_memory_review_decide",
    "bm_memory_review_apply",
    "bm_memory_review_status",
})


def _guard(output_format: OutputFormat) -> dict[str, Any] | None:
    if not lifecycle_enabled():
        return {
            "ok": False,
            "code": "LIFECYCLE_DISABLED",
            "message": "Set BASIC_MEMORY_LIFECYCLE=1 to enable lifecycle review tools.",
        }
    return None


async def bm_memory_review_propose(
    proposals: Annotated[list[dict[str, Any]], Field(description="Proposal objects.")],
    output_format: OutputFormat = "text",
) -> str | dict[str, Any]:
    blocked = _guard(output_format)
    if blocked:
        return blocked
    added = ReviewQueue().propose(proposals)
    result = {
        "ok": True,
        "queued": len(added),
        "ids": [p.id for p in added],
    }
    if output_format == "json":
        return result
    return f"Queued {len(added)} proposal(s): " + ", ".join(p.id for p in added)


async def bm_memory_review_list(
    output_format: OutputFormat = "text",
) -> str | dict[str, Any]:
    blocked = _guard(output_format)
    if blocked:
        return blocked
    pending = ReviewQueue().list_pending()
    items = [p.to_dict() for p in pending]
    if output_format == "json":
        return {"ok": True, "pending": items}
    if not items:
        return "No pending lifecycle proposals."
    return "\n".join(
        f"{i}. [{p['operation']}] {p.get('permalink') or '(new)'} — {p['reason']} (hits={p['hits']})"
        for i, p in enumerate(items, start=1)
    )


async def bm_memory_review_decide(
    proposal_id: Annotated[str, Field(min_length=1)],
    approve: Annotated[bool, Field(description="True=approve, False=reject.")] = True,
    output_format: OutputFormat = "text",
) -> str | dict[str, Any]:
    blocked = _guard(output_format)
    if blocked:
        return blocked
    updated = ReviewQueue().decide(proposal_id, approve)
    if updated is None:
        return {"ok": False, "message": "proposal not found"} if output_format == "json" else "Proposal not found."
    result = {"ok": True, "proposal": updated.to_dict()}
    if output_format == "json":
        return result
    return f"Proposal {proposal_id} {'approved' if approve else 'rejected'}."


async def bm_memory_review_apply(
    proposal_id: Annotated[str, Field(min_length=1)],
    output_format: OutputFormat = "text",
) -> str | dict[str, Any]:
    blocked = _guard(output_format)
    if blocked:
        return blocked
    queue = ReviewQueue()
    proposal = queue.get(proposal_id)
    if proposal is None:
        return {"ok": False, "message": "proposal not found"} if output_format == "json" else "Proposal not found."
    if proposal.status != "approved":
        msg = "proposal is not approved"
        return {"ok": False, "message": msg} if output_format == "json" else msg
    if proposal.operation == "new":
        msg = "new-note proposals are created with bm_write, not set_metadata"
        return {"ok": False, "message": msg, "proposal": proposal.to_dict()} if output_format == "json" else msg
    patch = ReviewQueue.applies_to_metadata(proposal)
    result = {
        "ok": True,
        "proposal_id": proposal_id,
        "identifier": proposal.permalink,
        "metadata": patch,
        "next_call": "bm_edit(command='set_metadata', metadata=<metadata>)",
    }
    if output_format == "json":
        return result
    return f"{proposal_id}: apply metadata {patch} with bm_edit(set_metadata) on {proposal.permalink}"


async def bm_memory_review_status(
    output_format: OutputFormat = "text",
) -> str | dict[str, Any]:
    blocked = _guard(output_format)
    if blocked:
        return blocked
    queue = ReviewQueue()
    counts = queue.counts()
    if output_format == "json":
        return {
            "ok": True,
            "enabled": True,
            "queue_path": str(queue.path),
            "counts": counts,
        }
    if not counts:
        return f"Lifecycle review enabled; queue {queue.path} has no proposals."
    breakdown = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
    return f"Lifecycle review enabled; queue {queue.path}: {breakdown}"


def register_review_tools(server: FastMCP) -> None:
    """Register the lifecycle review tools on any FastMCP server."""
    server.tool(
        name="bm_memory_review_propose",
        title="Propose memory lifecycle changes",
        description=(
            "Queue lifecycle change proposals (new/update/status_progression/archive). "
            "Each proposal needs operation and metadata; evidence should cite the source note "
            "and quote. No notes are modified. Requires BASIC_MEMORY_LIFECYCLE=1."
        ),
        tags={"lifecycle"},
        annotations={"title": "Propose memory lifecycle changes", "readOnlyHint": False,
                     "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    )(bm_memory_review_propose)

    server.tool(
        name="bm_memory_review_list",
        title="List pending lifecycle reviews",
        description="List pending lifecycle proposals with their evidence. No writes.",
        tags={"lifecycle"},
        annotations={"title": "List pending lifecycle reviews", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )(bm_memory_review_list)

    server.tool(
        name="bm_memory_review_decide",
        title="Decide a lifecycle proposal",
        description="Approve or reject a proposal by id. No notes are modified here.",
        tags={"lifecycle"},
        annotations={"title": "Decide a lifecycle proposal", "readOnlyHint": False,
                     "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    )(bm_memory_review_decide)

    server.tool(
        name="bm_memory_review_apply",
        title="Get approved lifecycle patch to apply",
        description=(
            "Validate an approved proposal and return the metadata patch to apply with "
            "bm_edit(command='set_metadata', metadata=<patch>). New-note proposals must "
            "be created with bm_write instead. Requires BASIC_MEMORY_LIFECYCLE=1."
        ),
        tags={"lifecycle"},
        annotations={"title": "Get approved lifecycle patch to apply", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )(bm_memory_review_apply)

    server.tool(
        name="bm_memory_review_status",
        title="Show lifecycle review status",
        description="Report lifecycle feature state and review queue counts by status. No writes.",
        tags={"lifecycle"},
        annotations={"title": "Show lifecycle review status", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )(bm_memory_review_status)


__all__ = [
    "REVIEW_TOOL_NAMES",
    "bm_memory_review_propose",
    "bm_memory_review_list",
    "bm_memory_review_decide",
    "bm_memory_review_apply",
    "bm_memory_review_status",
    "register_review_tools",
]
