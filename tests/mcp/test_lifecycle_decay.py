"""Explicit lifecycle weight decay through bm_memory_decay.

The decay path was unreachable before this tool: `decay_update` had no caller
outside a deprecated service, so a note's weight only ever rose. These tests
cover the write itself, the archive threshold, and the branches that decline to
write.
"""

from __future__ import annotations

from typing import Any

import pytest

from basic_memory.lifecycle import (
    ARCHIVE_THRESHOLD,
    CANON_ARCHIVED,
    FIELD_ARCHIVED_AT,
    FIELD_CANON_STATUS,
    FIELD_LAST_DECAY,
    FIELD_WEIGHT,
)
from basic_memory.mcp.tools import bm_memory_decay, bm_read, bm_write


def _note(title: str, weight: float, last_hit: str, canon_status: str = "canon") -> str:
    return (
        f"---\ntitle: {title}\n"
        f"weight: {weight}\nbase_weight: {weight}\n"
        f"last_hit: {last_hit}\n"
        f"canon_status: {canon_status}\n---\n\n"
        f"# {title}\n\n- [fact] body stays\n"
    )


async def decay_json(monkeypatch: pytest.MonkeyPatch, **arguments: Any) -> dict[str, Any]:
    monkeypatch.setenv("BASIC_MEMORY_LIFECYCLE", "1")
    result = await bm_memory_decay(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


@pytest.mark.asyncio
async def test_decay_disabled_returns_lifecycle_code(monkeypatch, client, test_project):
    await bm_write(
        identifier="notes/decay.md",
        content=_note("Decay Note", 2.0, "2026-01-01T00:00:00Z"),
        project=test_project.name,
    )
    monkeypatch.delenv("BASIC_MEMORY_LIFECYCLE", raising=False)
    result = await bm_memory_decay(
        "notes/decay.md", output_format="json", project=test_project.name
    )
    assert isinstance(result, dict)
    assert result["code"] == "LIFECYCLE_DISABLED"


@pytest.mark.asyncio
async def test_decay_lowers_weight_and_keeps_body(monkeypatch, client, test_project):
    """A note unused for long enough loses weight, and its body is untouched."""
    await bm_write(
        identifier="notes/decay.md",
        content=_note("Decay Note", 2.5, "2026-01-01T00:00:00Z"),
        project=test_project.name,
    )
    body_before = await bm_read(
        "notes/decay.md", include_frontmatter=False, project=test_project.name
    )

    result = await decay_json(monkeypatch, identifier="notes/decay.md", project=test_project.name)

    assert result["ok"] is True
    assert result["metadata"][FIELD_WEIGHT] < 2.5
    assert FIELD_LAST_DECAY in result["metadata"]

    stored = await bm_read("notes/decay.md", project=test_project.name)
    assert isinstance(stored, str)
    assert f"weight: {result['metadata'][FIELD_WEIGHT]}" in stored
    assert (
        await bm_read("notes/decay.md", include_frontmatter=False, project=test_project.name)
        == body_before
    )


@pytest.mark.asyncio
async def test_decay_archives_below_threshold(monkeypatch, client, test_project):
    """A weight that falls under the archive threshold archives the note."""
    await bm_write(
        identifier="notes/decay.md",
        content=_note("Doomed Note", 1.2, "2026-01-01T00:00:00Z"),
        project=test_project.name,
    )

    result = await decay_json(monkeypatch, identifier="notes/decay.md", project=test_project.name)

    assert result["ok"] is True
    assert result["metadata"][FIELD_WEIGHT] < ARCHIVE_THRESHOLD
    assert result["metadata"][FIELD_CANON_STATUS] == CANON_ARCHIVED
    assert FIELD_ARCHIVED_AT in result["metadata"]

    stored = await bm_read("notes/decay.md", project=test_project.name)
    assert isinstance(stored, str)
    assert f"canon_status: {CANON_ARCHIVED}" in stored


@pytest.mark.asyncio
async def test_decay_reports_no_interval_without_writing(monkeypatch, client, test_project):
    """A note hit moments ago has no elapsed interval; nothing is written."""
    from basic_memory.lifecycle import _iso, _now

    await bm_write(
        identifier="notes/decay.md",
        content=_note("Fresh Note", 2.0, _iso(_now())),
        project=test_project.name,
    )
    before = await bm_read("notes/decay.md", project=test_project.name)

    result = await decay_json(monkeypatch, identifier="notes/decay.md", project=test_project.name)

    assert result["ok"] is False
    assert result["code"] == "NO_DECAY"
    assert "no decay interval" in result["message"]
    assert await bm_read("notes/decay.md", project=test_project.name) == before


@pytest.mark.asyncio
async def test_decay_refuses_an_archived_note(monkeypatch, client, test_project):
    """An archived note is not decayed further."""
    await bm_write(
        identifier="notes/decay.md",
        content=_note("Archived Note", 0.5, "2026-01-01T00:00:00Z", canon_status=CANON_ARCHIVED),
        project=test_project.name,
    )
    before = await bm_read("notes/decay.md", project=test_project.name)

    result = await decay_json(monkeypatch, identifier="notes/decay.md", project=test_project.name)

    assert result["ok"] is False
    assert result["code"] == "NO_DECAY"
    assert "archived" in result["message"]
    assert await bm_read("notes/decay.md", project=test_project.name) == before


@pytest.mark.asyncio
async def test_decay_force_applies_one_day(monkeypatch, client, test_project):
    """force decays one day even though no interval has elapsed."""
    from basic_memory.lifecycle import DECAY_PER_DAY, _iso, _now

    await bm_write(
        identifier="notes/decay.md",
        content=_note("Forced Note", 2.0, _iso(_now())),
        project=test_project.name,
    )

    result = await decay_json(
        monkeypatch, identifier="notes/decay.md", force=True, project=test_project.name
    )

    assert result["ok"] is True
    assert result["metadata"][FIELD_WEIGHT] == pytest.approx(2.0 - DECAY_PER_DAY)
