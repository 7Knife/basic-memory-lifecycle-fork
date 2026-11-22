"""Project isolation and failure reporting at the review tool layer.

The queue file is shared, so the tools are where a proposal from one project
must stop reaching another, and where a broken queue file must be reported
rather than read as an empty queue.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from basic_memory.lifecycle import FIELD_WEIGHT
from basic_memory.mcp.tools import (
    bm_memory_review_apply,
    bm_memory_review_decide,
    bm_memory_review_list,
    bm_memory_review_propose,
    bm_memory_review_status,
)

PROPOSAL = {
    "operation": "update",
    "permalink": "notes/plan.md",
    "metadata": {FIELD_WEIGHT: 2.0},
    "reason": "weight changed",
}


@pytest.fixture
def queue_path(tmp_path, monkeypatch) -> Path:
    """A queue file the review tools read and write."""
    path = tmp_path / "queue.json"
    monkeypatch.setenv("BASIC_MEMORY_LIFECYCLE", "1")
    monkeypatch.setenv("BASIC_MEMORY_REVIEW_QUEUE", str(path))
    return path


async def _propose(project: str) -> dict[str, Any]:
    result = await bm_memory_review_propose(
        [PROPOSAL], project=project, output_format="json"
    )
    assert isinstance(result, dict)
    return result


@pytest.mark.asyncio
async def test_a_proposal_is_invisible_to_another_project(queue_path):
    """A project does not see a proposal another project made."""
    proposed = await _propose("alpha")
    assert proposed["ok"] is True

    listed = await bm_memory_review_list(project="beta", output_format="json")
    assert isinstance(listed, dict)
    assert listed["pending"] == []

    listed = await bm_memory_review_list(project="alpha", output_format="json")
    assert isinstance(listed, dict)
    assert len(listed["pending"]) == 1


@pytest.mark.asyncio
async def test_deciding_another_projects_proposal_is_refused(queue_path):
    """Deciding another project's proposal is refused with the reason."""
    proposed = await _propose("alpha")
    proposal_id = proposed["ids"][0]

    decided = await bm_memory_review_decide(
        proposal_id, True, project="beta", output_format="json"
    )
    assert isinstance(decided, dict)
    assert decided["ok"] is False
    assert decided["code"] == "QUEUE_ERROR"
    assert "alpha" in decided["message"]

    # The proposal is untouched.
    listed = await bm_memory_review_list(project="alpha", output_format="json")
    assert isinstance(listed, dict)
    assert listed["pending"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_a_proposal_is_decided_in_its_own_project(queue_path):
    proposed = await _propose("alpha")
    proposal_id = proposed["ids"][0]

    decided = await bm_memory_review_decide(
        proposal_id, True, project="alpha", output_format="json"
    )
    assert isinstance(decided, dict)
    assert decided["ok"] is True
    assert decided["proposal"]["status"] == "approved"

    applied = await bm_memory_review_apply(proposal_id, project="alpha", output_format="json")
    assert isinstance(applied, dict)
    assert applied["ok"] is True
    assert applied["metadata"] == {FIELD_WEIGHT: 2.0}


@pytest.mark.asyncio
async def test_a_decided_proposal_cannot_be_decided_again(queue_path):
    """A late call cannot reopen a proposal that was already decided."""
    proposed = await _propose("alpha")
    proposal_id = proposed["ids"][0]
    await bm_memory_review_decide(proposal_id, True, project="alpha", output_format="json")

    again = await bm_memory_review_decide(
        proposal_id, False, project="alpha", output_format="json"
    )
    assert isinstance(again, dict)
    assert again["ok"] is False
    assert "approved" in again["message"]


@pytest.mark.asyncio
async def test_a_corrupt_queue_is_reported(queue_path):
    """A queue file that cannot be parsed is reported, not read as empty."""
    queue_path.write_text("{not json", encoding="utf-8")

    listed = await bm_memory_review_list(project="alpha", output_format="json")
    assert isinstance(listed, dict)
    assert listed["ok"] is False
    assert listed["code"] == "QUEUE_ERROR"
    assert str(queue_path) in listed["message"]

    # The file is left as it was.
    assert queue_path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.asyncio
async def test_a_corrupt_queue_is_not_overwritten_by_a_write(queue_path):
    """A write against a broken queue fails and leaves the file alone."""
    queue_path.write_text("{not json", encoding="utf-8")

    proposed = await bm_memory_review_propose(
        [PROPOSAL], project="alpha", output_format="json"
    )
    assert isinstance(proposed, dict)
    assert proposed["ok"] is False
    assert queue_path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.asyncio
async def test_status_counts_only_the_scoped_project(queue_path):
    await _propose("alpha")
    await _propose("beta")

    status = await bm_memory_review_status(project="alpha", output_format="json")
    assert isinstance(status, dict)
    assert status["counts"] == {"pending": 1}
    assert status["project"] == "alpha"


@pytest.mark.asyncio
async def test_an_unowned_proposal_is_refused_with_a_repropose_hint(queue_path):
    """A proposal from before projects existed has no owner and is refused."""
    queue_path.write_text(
        json.dumps(
            [
                {
                    "id": "legacy-1",
                    "operation": "update",
                    "permalink": "notes/plan.md",
                    "metadata": {FIELD_WEIGHT: 2.0},
                    "reason": "from an older queue",
                    "status": "pending",
                }
            ]
        ),
        encoding="utf-8",
    )

    decided = await bm_memory_review_decide(
        "legacy-1", True, project="alpha", output_format="json"
    )
    assert isinstance(decided, dict)
    assert decided["ok"] is False
    assert "propose it again" in decided["message"]


@pytest.mark.asyncio
async def test_applying_without_a_project_is_refused(queue_path):
    """Applying a patch needs to know which project's notes it is for."""
    proposed = await _propose("alpha")
    proposal_id = proposed["ids"][0]
    await bm_memory_review_decide(
        proposal_id, True, project="alpha", output_format="json"
    )

    # No project named, and none configured: the queue cannot say which
    # project's notes the patch is for.
    applied = await bm_memory_review_apply(proposal_id, output_format="json")
    assert isinstance(applied, dict)
    assert applied["ok"] is False
    assert "project" in applied["message"]
