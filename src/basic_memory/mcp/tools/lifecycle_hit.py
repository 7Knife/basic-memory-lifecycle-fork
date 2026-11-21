"""Explicit lifecycle hit recording for notes used as memory.

This tool records that a note was actually consulted after a search hit. It is
deliberately separate from bm_search, which stays read-only, and from the review
queue, which gates changes to canon metadata. The write merges lifecycle fields
into frontmatter through the Knowledge API's set_metadata path, preserving Basic
Memory's prepare -> persist -> materialize chain.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError

from basic_memory.lifecycle import hit_bonus_update, lifecycle_enabled
from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.note_targets import open_project_target, resolve_note
from basic_memory.mcp.refusals import note_change_failure, refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import NoteIdentifier, OptionalProjectParam, OutputFormat
from basic_memory.runtime.storage import runtime_file_path_is_markdown_note


@mcp.tool(
    name="bm_memory_review_hit",
    title="Record a lifecycle retrieval hit",
    description=(
        "Record that a note was consulted after bm_search found it. Merges last_hit "
        "and a capped weight bonus into the note's frontmatter through set_metadata; "
        "notes already at their weight cap still get last_hit refreshed. Requires "
        "BASIC_MEMORY_LIFECYCLE=1."
    ),
    tags={"lifecycle"},
    annotations={
        "title": "Record a lifecycle retrieval hit",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def bm_memory_review_hit(
    identifier: NoteIdentifier,
    output_format: OutputFormat = "text",
    project: OptionalProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Record one retrieval hit on an existing Markdown note."""
    if not lifecycle_enabled():
        return {
            "ok": False,
            "code": "LIFECYCLE_DISABLED",
            "message": "Set BASIC_MEMORY_LIFECYCLE=1 to enable lifecycle hit recording.",
        }

    async with open_project_target(
        identifier, project, context, kind="identifier", output_format=output_format
    ) as target:
        note = await resolve_note(target, identifier, output_format=output_format)
        if not runtime_file_path_is_markdown_note(note.file_path):
            raise refusal(
                "UNSUPPORTED",
                f"'{target.paths.path(note.file_path)}' is not a Markdown note, so it has no "
                "lifecycle metadata.",
                try_instead="record hits on Markdown notes only",
                output_format=output_format,
            )

        knowledge = KnowledgeClient(target.client, target.project.external_id)
        entity = await knowledge.get_entity(note.external_id)
        update = hit_bonus_update(dict(entity.entity_metadata or {}))
        if update is None:
            return (
                {"ok": False, "message": "note is archived"} if output_format == "json"
                else f"{target.paths.path(note.file_path)} is archived; no hit recorded."
            )
        try:
            edited = await knowledge.patch_entity(
                note.external_id,
                {"operation": "set_metadata", "content": "", "metadata": update},
                base_checksum=entity.db_checksum,
            )
        except ToolError as error:
            raise note_change_failure(
                error,
                action="edit",
                address=target.paths.path(note.file_path),
                unchanged="No lifecycle hit was recorded.",
                output_format=output_format,
                project=project,
            ) from error

    result = {
        "ok": True,
        "identifier": identifier,
        "file_path": target.paths.path(edited.file_path),
        "metadata": update,
    }
    if output_format == "json":
        return result
    fields = ", ".join(f"{key}={value}" for key, value in update.items())
    return f"Recorded lifecycle hit on {result['file_path']}: {fields}"


__all__ = ["bm_memory_review_hit"]
