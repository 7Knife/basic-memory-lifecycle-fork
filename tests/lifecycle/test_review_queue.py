"""Review queue isolation, locking and corruption handling (review findings 3-5).

The queue is one shared file, so a proposal made in one project must not be
visible or decidable in another, a concurrent writer must not lose a
proposal, and a file that cannot be parsed must be reported rather than
read as an empty queue and overwritten.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from basic_memory.lifecycle import (
    ProposalProjectError,
    ProposalStateError,
    ReviewQueue,
    ReviewQueueCorruptError,
    ReviewQueueError,
    project_scope_key,
)


def _workspace_tmp(name: str) -> Path:
    p = Path(__file__).resolve().parents[2] / ".lifecycle-test-tmp" / name
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _proposal(permalink: str = "notes/plan.md", weight: float = 2.0) -> dict:
    return {
        "operation": "update",
        "permalink": permalink,
        "metadata": {"weight": weight},
        "reason": "weight changed",
    }


def _write_proposals_in_process(queue_path: str, tag: str) -> None:
    """Queue proposals from a separate process (spawn cannot pickle a closure)."""
    queue = ReviewQueue(Path(queue_path))
    queue.propose([_proposal(permalink=f"notes/{tag}-{index}.md") for index in range(5)])


# --- Finding 3: project isolation ---


def test_proposals_are_scoped_to_their_project():
    """Two projects holding the same permalink do not see each other's proposals."""
    path = _workspace_tmp("scoped") / "queue.json"
    project_a = ReviewQueue(path, project="alpha")
    project_b = ReviewQueue(path, project="beta")

    added = project_a.propose([_proposal()])
    assert len(added) == 1

    assert len(project_a.list_pending()) == 1
    assert project_b.list_pending() == []

    # The other project's proposal is not found by id either.
    assert project_b.get(added[0].id) is None


def test_deciding_another_projects_proposal_is_refused():
    """Deciding a proposal another project owns is refused, not applied."""
    path = _workspace_tmp("scoped-decide") / "queue.json"
    project_a = ReviewQueue(path, project="alpha")
    project_b = ReviewQueue(path, project="beta")

    added = project_a.propose([_proposal()])

    with pytest.raises(ProposalProjectError) as error:
        project_b.decide(added[0].id, True)
    assert "alpha" in str(error.value)

    # The proposal is untouched.
    assert project_a.get(added[0].id).status == "pending"


def test_identical_proposals_in_two_projects_are_separate():
    """The same proposal in two projects is two proposals, not one with two hits."""
    path = _workspace_tmp("scoped-duplicate") / "queue.json"
    project_a = ReviewQueue(path, project="alpha")
    project_b = ReviewQueue(path, project="beta")

    project_a.propose([_proposal()])
    project_b.propose([_proposal()])

    assert len(project_a.list_pending()) == 1
    assert len(project_b.list_pending()) == 1


def test_counts_only_cover_the_scoped_project():
    path = _workspace_tmp("scoped-counts") / "queue.json"
    project_a = ReviewQueue(path, project="alpha")
    project_b = ReviewQueue(path, project="beta")

    project_a.propose([_proposal()])
    project_b.propose([_proposal()])

    assert project_a.counts() == {"pending": 1}
    assert project_b.counts() == {"pending": 1}


def test_an_unowned_proposal_is_refused_rather_than_guessed_at():
    """A proposal queued before projects existed has no owner, so it is refused."""
    path = _workspace_tmp("unowned") / "queue.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "legacy-1",
                    "operation": "update",
                    "permalink": "notes/plan.md",
                    "metadata": {"weight": 2.0},
                    "reason": "from an older queue",
                    "status": "pending",
                }
            ]
        ),
        encoding="utf-8",
    )
    queue = ReviewQueue(path, project="alpha")

    # It is not offered to a project, because no project owns it.
    assert queue.list_pending() == []

    with pytest.raises(ProposalProjectError):
        queue.decide("legacy-1", True)


def test_an_unscoped_queue_sees_every_proposal():
    """A queue with no project scope is not restricted."""
    path = _workspace_tmp("unscoped") / "queue.json"
    ReviewQueue(path, project="alpha").propose([_proposal()])
    ReviewQueue(path, project="beta").propose([_proposal()])

    assert len(ReviewQueue(path).list_pending()) == 2


def test_an_unscoped_queue_cannot_act_on_a_proposal():
    """A queue with no project cannot decide, because it cannot say which."""
    path = _workspace_tmp("unscoped-act") / "queue.json"
    scoped = ReviewQueue(path, project="alpha")
    added = scoped.propose([_proposal()])

    unscoped = ReviewQueue(path)
    with pytest.raises(ReviewQueueError) as error:
        unscoped.decide(added[0].id, True)
    assert "project" in str(error.value)

    # The proposal is untouched.
    assert scoped.get(added[0].id).status == "pending"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("alpha", "alpha"),
        ("Alpha", "alpha"),
        ("My Research", "my-research"),
        ("  alpha  ", "alpha"),
        ("workspace/alpha", "workspace/alpha"),
    ],
)
def test_project_scope_key_is_stable(name, expected):
    assert project_scope_key(name) == expected


def test_project_scope_key_of_nothing_is_nothing():
    assert project_scope_key(None) is None
    assert project_scope_key("   ") is None


# --- Finding 4: concurrent writers do not lose proposals ---


def test_concurrent_writers_keep_every_proposal():
    """Two processes writing the same queue at once keep both sets of proposals."""
    import multiprocessing

    path = _workspace_tmp("concurrent") / "queue.json"

    processes = [
        multiprocessing.Process(target=_write_proposals_in_process, args=(str(path), tag))
        for tag in ("a", "b")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0

    rows = json.loads(path.read_text(encoding="utf-8"))
    assert len(rows) == 10


def test_a_held_lock_is_reported_rather_than_waited_on_forever(monkeypatch):
    """A queue another process holds locked is refused, not waited on."""
    import basic_memory.lifecycle as lifecycle_module

    path = _workspace_tmp("locked") / "queue.json"
    monkeypatch.setattr(lifecycle_module, "LOCK_TIMEOUT_SECONDS", 0.05)

    queue = ReviewQueue(path)
    lock_path = queue.lock_path
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    handle = lock_path.open("a+b")
    try:
        lifecycle_module._try_lock_file_handle(handle.fileno())
        with pytest.raises(ReviewQueueError) as error:
            queue.propose([_proposal()])
        assert "locked by another process" in str(error.value)
    finally:
        handle.close()


# --- Finding 5: a corrupt queue is reported, not overwritten ---


def test_a_queue_of_invalid_json_is_reported():
    path = _workspace_tmp("corrupt-json") / "queue.json"
    path.write_text("{not json", encoding="utf-8")
    queue = ReviewQueue(path)

    with pytest.raises(ReviewQueueCorruptError) as error:
        queue.list_pending()
    assert str(path) in str(error.value)


def test_a_queue_that_is_not_a_list_is_reported():
    path = _workspace_tmp("corrupt-shape") / "queue.json"
    path.write_text('{"proposals": []}', encoding="utf-8")
    queue = ReviewQueue(path)

    with pytest.raises(ReviewQueueCorruptError):
        queue.counts()


def test_a_corrupt_queue_is_not_overwritten():
    """Reading a broken queue must not destroy what it holds."""
    path = _workspace_tmp("corrupt-preserve") / "queue.json"
    original = "{not json"
    path.write_text(original, encoding="utf-8")
    queue = ReviewQueue(path)

    with pytest.raises(ReviewQueueCorruptError):
        queue.propose([_proposal()])

    assert path.read_text(encoding="utf-8") == original


def test_a_missing_queue_is_an_empty_queue():
    """The normal state before the first proposal is an empty queue, not an error."""
    path = _workspace_tmp("missing") / "queue.json"
    queue = ReviewQueue(path)

    assert queue.list_pending() == []
    assert queue.counts() == {}


# --- Finding 6: the decision state machine ---


def test_a_decided_proposal_cannot_be_decided_again():
    """A late call cannot reopen a proposal that was already decided."""
    path = _workspace_tmp("state") / "queue.json"
    queue = ReviewQueue(path, project="alpha")
    added = queue.propose([_proposal()])

    queue.decide(added[0].id, True)

    with pytest.raises(ProposalStateError) as error:
        queue.decide(added[0].id, False)
    assert "approved" in str(error.value)
    assert queue.get(added[0].id).status == "approved"


def test_list_pending_reports_only_pending_proposals():
    path = _workspace_tmp("state-list") / "queue.json"
    queue = ReviewQueue(path, project="alpha")
    added = queue.propose([_proposal()])
    queue.decide(added[0].id, True)

    assert queue.list_pending() == []
    assert queue.counts() == {"approved": 1}
