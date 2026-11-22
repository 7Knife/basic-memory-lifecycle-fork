"""Explicit lifecycle weight decay for notes that are not being used.

This tool applies one decay step to a note's frontmatter: a note that has not
been consulted for long enough loses weight, and a note whose weight falls
below the archive threshold is archived. It is deliberately separate from
bm_search, which stays read-only, and from the review queue, which gates
changes to canon metadata. The write merges lifecycle fields into frontmatter
through the Knowledge API's set_metadata path, preserving Basic Memory's
prepare -> persist -> materialize chain.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError

from basic_memory.lifecycle import (
    CANON_ARCHIVED,
    FIELD_CANON_STATUS,
    decay_update,
    lifecycle_enabled,
)
from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.note_targets import open_project_target, resolve_note
from basic_memory.mcp.refusals import note_change_failure, refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import NoteIdentifier, OptionalProjectParam, OutputFormat
from basic_memory.runtime.storage import runtime_file_path_is_markdown_note


@mcp.tool(
    name="bm_memory_decay",
    title="Decay a note's lifecycle weight",
    description=(
        "Apply one lifecycle decay step to a note: a note that has gone long "
        "enough without a hit loses weight, and a note whose weight falls below "
        "the archive threshold is archived. Merges weight, last_decay_at and any "
        "archive fields into the note's frontmatter through set_metadata. A note "
        "with no elapsed interval is reported as unchanged. Requires "
        "BASIC_MEMORY_LIFECYCLE=1."
    ),
    tags={"lifecycle"},
    annotations={
        "title": "Decay a note's lifecycle weight",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def bm_memory_decay(
    identifier: NoteIdentifier,
    force: bool = False,
    output_format: OutputFormat = "text",
    project: OptionalProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Decay one note's lifecycle weight."""
    if not lifecycle_enabled():
        return {
            "ok": False,
            "code": "LIFECYCLE_DISABLED",
            "message": "Set BASIC_MEMORY_LIFECYCLE=1 to enable lifecycle decay.",
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
                try_instead="decay Markdown notes only",
                output_format=output_format,
            )

        knowledge = KnowledgeClient(target.client, target.project.external_id)
        entity = await knowledge.get_entity(note.external_id)
        metadata = dict(entity.entity_metadata or {})
        update = decay_update(metadata, force=force)
        if update is None:
            # The note is archived, or no interval has elapsed yet. Both are
            # real answers, so they are named rather than reported as a silent
            # no-change.
            reason = (
                "note is archived"
                if str(metadata.get(FIELD_CANON_STATUS, "") or "") == CANON_ARCHIVED
                else "no decay interval has elapsed since the last hit or decay"
            )
            return (
                {"ok": False, "code": "NO_DECAY", "message": reason}
                if output_format == "json"
                else f"{target.paths.path(note.file_path)}: {reason}; no decay was applied."
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
                unchanged="No lifecycle decay was applied.",
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
    return f"Decayed lifecycle weight on {result['file_path']}: {fields}"


__all__ = ["bm_memory_decay"]
