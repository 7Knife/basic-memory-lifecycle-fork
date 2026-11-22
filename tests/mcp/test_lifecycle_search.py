"""End-to-end lifecycle treatment through bm_search (review finding 1).

The indexer must carry weight/canon_status onto search rows; without that,
``sort_by="weight"`` and ``include_archived=False`` are silent no-ops in
production while the hand-built payload unit tests still pass.
"""

from __future__ import annotations

from typing import Any

import pytest

from basic_memory.mcp.tools import bm_search, bm_write


def _note(title: str, weight: float, canon_status: str) -> str:
    return (
        f"---\ntitle: {title}\n"
        f"weight: {weight}\nbase_weight: {weight}\n"
        f"canon_status: {canon_status}\n---\n\n"
        f"# {title}\n\n- [fact] shared body text\n"
    )


async def search_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_search(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


@pytest.mark.asyncio
async def test_weight_sort_orders_by_lifecycle_weight(monkeypatch, client, test_project):
    """bm_search(sort_by='weight') ranks by the note's frontmatter weight."""
    monkeypatch.setenv("BASIC_MEMORY_LIFECYCLE", "1")
    await bm_write(
        identifier="notes/low.md",
        content=_note("Low Weight", 1.0, "canon"),
        project=test_project.name,
    )
    await bm_write(
        identifier="notes/high.md",
        content=_note("High Weight", 3.0, "canon"),
        project=test_project.name,
    )

    payload = await search_json(
        query="shared", mode="text", sort_by="weight", project=test_project.name
    )

    assert [row["title"] for row in payload["results"]] == ["High Weight", "Low Weight"]
    # The lifecycle fields actually traveled onto the search row.
    assert payload["results"][0]["metadata"]["weight"] == 3.0


@pytest.mark.asyncio
async def test_include_archived_hides_archived_notes(monkeypatch, client, test_project):
    """bm_search(include_archived=False) hides notes with canon_status=archived."""
    monkeypatch.setenv("BASIC_MEMORY_LIFECYCLE", "1")
    await bm_write(
        identifier="notes/canon.md",
        content=_note("Canon Note", 2.0, "canon"),
        project=test_project.name,
    )
    await bm_write(
        identifier="notes/old.md",
        content=_note("Archived Note", 2.0, "archived"),
        project=test_project.name,
    )

    hidden = await search_json(
        query="shared", mode="text", sort_by="weight", project=test_project.name
    )
    assert [row["title"] for row in hidden["results"]] == ["Canon Note"]
    assert hidden["total_is_exact"] is False

    shown = await search_json(
        query="shared",
        mode="text",
        sort_by="weight",
        include_archived=True,
        project=test_project.name,
    )
    assert {row["title"] for row in shown["results"]} == {"Canon Note", "Archived Note"}
