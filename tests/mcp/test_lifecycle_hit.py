"""Explicit lifecycle hit recording through bm_memory_review_hit."""

from __future__ import annotations

from typing import Any

import pytest

from basic_memory.lifecycle import FIELD_LAST_HIT, FIELD_WEIGHT
from basic_memory.mcp.tools import bm_memory_review_hit, bm_read, bm_write

NOTE = (
    "---\ntitle: Hit Note\n"
    "weight: 2.0\nbase_weight: 2.0\ncanon_status: canon\n---\n\n"
    "# Hit Note\n\n- [fact] body stays\n"
)


async def hit_json(monkeypatch: pytest.MonkeyPatch, **arguments: Any) -> dict[str, Any]:
    monkeypatch.setenv("BASIC_MEMORY_LIFECYCLE", "1")
    result = await bm_memory_review_hit(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


@pytest.mark.asyncio
async def test_hit_disabled_returns_lifecycle_code(monkeypatch, client, test_project):
    await bm_write(identifier="notes/hit.md", content=NOTE, project=test_project.name)
    monkeypatch.delenv("BASIC_MEMORY_LIFECYCLE", raising=False)
    result = await bm_memory_review_hit(
        "notes/hit.md", output_format="json", project=test_project.name
    )
    assert isinstance(result, dict)
    assert result["code"] == "LIFECYCLE_DISABLED"


@pytest.mark.asyncio
async def test_hit_updates_frontmatter_and_keeps_body(monkeypatch, client, test_project):
    await bm_write(identifier="notes/hit.md", content=NOTE, project=test_project.name)
    body_before = await bm_read(
        "notes/hit.md", include_frontmatter=False, project=test_project.name
    )

    result = await hit_json(monkeypatch, identifier="notes/hit.md", project=test_project.name)
    assert result["ok"] is True
    assert result["metadata"][FIELD_WEIGHT] == 2.5
    assert FIELD_LAST_HIT in result["metadata"]

    stored = await bm_read("notes/hit.md", project=test_project.name)
    assert isinstance(stored, str)
    assert "weight: 2.5" in stored
    assert FIELD_LAST_HIT in stored
    assert await bm_read(
        "notes/hit.md", include_frontmatter=False, project=test_project.name
    ) == body_before

    # A second hit respects the base-weight cap; last_hit is still refreshed.
    second = await hit_json(monkeypatch, identifier="notes/hit.md", project=test_project.name)
    assert second["metadata"][FIELD_WEIGHT] == 3.0
    third = await hit_json(monkeypatch, identifier="notes/hit.md", project=test_project.name)
    assert FIELD_WEIGHT not in third["metadata"]
    assert FIELD_LAST_HIT in third["metadata"]
