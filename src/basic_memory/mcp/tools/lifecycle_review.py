"""Generic lifecycle review MCP tools.

These tools manage a JSON-backed proposal queue with an explicit
propose -> decide -> apply gate. Lifecycle is opt-in via BASIC_MEMORY_LIFECYCLE=1.

`bm_memory_review_propose`, `bm_memory_review_list`, `bm_memory_review_decide`
operate on the queue only. `bm_memory_review_apply` returns the approved metadata
patch to apply with `bm_edit(command='set_metadata')`, which uses Basic Memory's
standard note-write pipeline. This keeps the tool independent of project container
internals while preserving the confirmation buffer semantics.

Proposals belong to a project. Each tool takes the project it acts on, and the
queue only shows and accepts that project's proposals, so a proposal made in one
project cannot be applied to a note of the same name in another.

The tools are plain functions registered through `register_review_tools(server)`,
so the same surface mounts on the main Basic Memory server (via the tool
registry) or on the standalone `basic_memory.mcp.review_server` without
duplicating definitions.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from basic_memory.config import ConfigManager
from basic_memory.lifecycle import (
    ProposalProjectError,
    ReviewQueue,
    ReviewQueueCorruptError,
    ReviewQueueError,
    lifecycle_enabled,
    project_scope_key,
)
from basic_memory.mcp.tool_params import OutputFormat

REVIEW_TOOL_NAMES = frozenset(
    {
        "bm_memory_review_propose",
        "bm_memory_review_list",
        "bm_memory_review_decide",
        "bm_memory_review_apply",
        "bm_memory_review_status",
    }
)


def _guard(output_format: OutputFormat) -> dict[str, Any] | None:
    if not lifecycle_enabled():
        return {
            "ok": False,
            "code": "LIFECYCLE_DISABLED",
            "message": "Set BASIC_MEMORY_LIFECYCLE=1 to enable lifecycle review tools.",
        }
    return None


def _scope(project: str | None) -> str | None:
    """The project the queue is scoped to for this call.

    The named project wins. Otherwise the configured default project applies,
    which both the main server and the standalone review server read from the
    same configuration file, so they scope the queue identically.
    """
    if project and project.strip():
        return project_scope_key(project)
    try:
        default_project = ConfigManager().config.default_project
    except OSError:
        # The configuration could not be read (a read-only home, say). There is
        # no default project to scope to, so the queue is unscoped rather than
        # the call failing over a project the caller did not name.
        return None
    return project_scope_key(default_project) if default_project else None


def _queue(project: str | None) -> ReviewQueue:
    return ReviewQueue(project=_scope(project))


def _queue_failure(error: ReviewQueueError, output_format: OutputFormat) -> str | dict[str, Any]:
    """A queue failure the caller can act on, as a refusal rather than a crash.

    A corrupt queue file and a proposal owned by another project are both
    answers the caller needs to read, not faults to hide behind an empty
    result.
    """
    if isinstance(error, ReviewQueueCorruptError):
        message = f"{error}. The file was left untouched; repair or move it, then try again."
    elif isinstance(error, ProposalProjectError) and error.owner is None:
        message = (
            f"{error}. It was queued before proposals carried a project, "
            "so propose it again in this project."
        )
    else:
        message = f"{error}."
    if output_format == "json":
        return {"ok": False, "code": "QUEUE_ERROR", "message": message}
    return message


async def bm_memory_review_propose(
    proposals: Annotated[list[dict[str, Any]], Field(description="Proposal objects.")],
    project: Annotated[
        str | None,
        Field(description="The project the proposals belong to. Defaults to the default project."),
    ] = None,
    output_format: OutputFormat = "text",
) -> str | dict[str, Any]:
    blocked = _guard(output_format)
    if blocked:
        return blocked
    try:
        added = _queue(project).propose(proposals)
    except ReviewQueueError as error:
        return _queue_failure(error, output_format)
    result = {
        "ok": True,
        "queued": len(added),
        "ids": [p.id for p in added],
    }
    if output_format == "json":
        return result
    return f"Queued {len(added)} proposal(s): " + ", ".join(p.id for p in added)


async def bm_memory_review_list(
    project: Annotated[
        str | None,
        Field(description="The project whose proposals to list. Defaults to the default project."),
    ] = None,
    output_format: OutputFormat = "text",
) -> str | dict[str, Any]:
    blocked = _guard(output_format)
    if blocked:
        return blocked
    queue = _queue(project)
    try:
        pending = queue.list_pending()
    except ReviewQueueError as error:
        return _queue_failure(error, output_format)
    items = [p.to_dict() for p in pending]
    if output_format == "json":
        return {"ok": True, "project": queue.project, "pending": items}
    if not items:
        return "No pending lifecycle proposals."
    return "\n".join(
        f"{i}. [{p['operation']}] {p.get('permalink') or '(new)'} — {p['reason']} (hits={p['hits']})"
        for i, p in enumerate(items, start=1)
    )


async def bm_memory_review_decide(
    proposal_id: Annotated[str, Field(min_length=1)],
    approve: Annotated[bool, Field(description="True=approve, False=reject.")] = True,
    project: Annotated[
        str | None,
        Field(description="The project the proposal belongs to. Defaults to the default project."),
    ] = None,
    output_format: OutputFormat = "text",
) -> str | dict[str, Any]:
    blocked = _guard(output_format)
    if blocked:
        return blocked
    try:
        updated = _queue(project).decide(proposal_id, approve)
    except ReviewQueueError as error:
        return _queue_failure(error, output_format)
    if updated is None:
        return (
            {"ok": False, "message": "proposal not found"}
            if output_format == "json"
            else "Proposal not found."
        )
    result = {"ok": True, "proposal": updated.to_dict()}
    if output_format == "json":
        return result
    return f"Proposal {proposal_id} {'approved' if approve else 'rejected'}."


async def bm_memory_review_apply(
    proposal_id: Annotated[str, Field(min_length=1)],
    project: Annotated[
        str | None,
        Field(description="The project the proposal belongs to. Defaults to the default project."),
    ] = None,
    output_format: OutputFormat = "text",
) -> str | dict[str, Any]:
    blocked = _guard(output_format)
    if blocked:
        return blocked
    queue = _queue(project)
    try:
        queue.require_project()
        proposal = queue.get(proposal_id)
    except ReviewQueueError as error:
        return _queue_failure(error, output_format)
    if proposal is None:
        return (
            {"ok": False, "message": "proposal not found"}
            if output_format == "json"
            else "Proposal not found."
        )
    if proposal.status != "approved":
        msg = "proposal is not approved"
        return {"ok": False, "message": msg} if output_format == "json" else msg
    if proposal.operation == "new":
        msg = "new-note proposals are created with bm_write, not set_metadata"
        return (
            {"ok": False, "message": msg, "proposal": proposal.to_dict()}
            if output_format == "json"
            else msg
        )
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
    return (
        f"{proposal_id}: apply metadata {patch} with bm_edit(set_metadata) on {proposal.permalink}"
    )


async def bm_memory_review_status(
    project: Annotated[
        str | None,
        Field(description="The project whose proposals to count. Defaults to the default project."),
    ] = None,
    output_format: OutputFormat = "text",
) -> str | dict[str, Any]:
    blocked = _guard(output_format)
    if blocked:
        return blocked
    queue = _queue(project)
    try:
        counts = queue.counts()
    except ReviewQueueError as error:
        return _queue_failure(error, output_format)
    if output_format == "json":
        return {
            "ok": True,
            "enabled": True,
            "queue_path": str(queue.path),
            "project": queue.project,
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
            "and quote. No notes are modified. Proposals belong to one project; name it with "
            "`project`. Requires BASIC_MEMORY_LIFECYCLE=1."
        ),
        tags={"lifecycle"},
        annotations={
            "title": "Propose memory lifecycle changes",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )(bm_memory_review_propose)

    server.tool(
        name="bm_memory_review_list",
        title="List pending lifecycle reviews",
        description=(
            "List pending lifecycle proposals with their evidence. Shows one project's "
            "proposals; name it with `project`. No writes."
        ),
        tags={"lifecycle"},
        annotations={
            "title": "List pending lifecycle reviews",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )(bm_memory_review_list)

    server.tool(
        name="bm_memory_review_decide",
        title="Decide a lifecycle proposal",
        description=(
            "Approve or reject a proposal by id. Name its project with `project`. "
            "No notes are modified here."
        ),
        tags={"lifecycle"},
        annotations={
            "title": "Decide a lifecycle proposal",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )(bm_memory_review_decide)

    server.tool(
        name="bm_memory_review_apply",
        title="Get approved lifecycle patch to apply",
        description=(
            "Validate an approved proposal and return the metadata patch to apply with "
            "bm_edit(command='set_metadata', metadata=<patch>). Name the proposal's project "
            "with `project`. New-note proposals must be created with bm_write instead. "
            "Requires BASIC_MEMORY_LIFECYCLE=1."
        ),
        tags={"lifecycle"},
        annotations={
            "title": "Get approved lifecycle patch to apply",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )(bm_memory_review_apply)

    server.tool(
        name="bm_memory_review_status",
        title="Show lifecycle review status",
        description=(
            "Report lifecycle feature state and review queue counts by status for one "
            "project. No writes."
        ),
        tags={"lifecycle"},
        annotations={
            "title": "Show lifecycle review status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
