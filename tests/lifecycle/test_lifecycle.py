"""Tests for the generic lifecycle extension in Basic Memory."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from basic_memory.lifecycle import (
    CANON_ARCHIVED,
    CANON_CANON,
    FIELD_BASE_WEIGHT,
    FIELD_CANON_STATUS,
    FIELD_LAST_HIT,
    FIELD_WEIGHT,
    ReviewQueue,
    decay_update,
    hit_bonus_update,
)
from basic_memory.mcp.tools.ranked_search import _apply_lifecycle_to_payload


NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)


def _meta(**overrides):
    meta = {
        FIELD_WEIGHT: 3.0,
        FIELD_BASE_WEIGHT: 3.0,
        FIELD_LAST_HIT: "2026-11-16T00:00:00Z",
        FIELD_CANON_STATUS: CANON_CANON,
    }
    meta.update(overrides)
    return meta


def test_decay_is_idempotent_within_interval():
    first = decay_update(_meta(), now=NOW)
    assert first is not None
    assert first[FIELD_WEIGHT] == pytest.approx(2.7)
    again = decay_update({**_meta(), **first}, now=NOW)
    assert again is None
    later = decay_update({**_meta(), **first}, now=datetime(2026, 11, 23, tzinfo=timezone.utc))
    assert later is not None
    assert later[FIELD_WEIGHT] == pytest.approx(2.4)


def test_decay_archives_below_threshold():
    update = decay_update(_meta(weight=1.2, base_weight=1.5, last_hit="2026-01-01T00:00:00Z"), now=NOW)
    assert update is not None
    assert update[FIELD_CANON_STATUS] == CANON_ARCHIVED
    assert "archived_at" in update


def test_hit_bonus_respects_cap():
    update = hit_bonus_update(_meta(weight=2.0, base_weight=2.0), now=NOW)
    assert update[FIELD_WEIGHT] == pytest.approx(2.5)
    assert update[FIELD_LAST_HIT] == "2026-11-20T00:00:00Z"
    capped = hit_bonus_update(_meta(weight=3.0, base_weight=3.0), now=NOW)
    assert FIELD_WEIGHT not in capped  # already at MAX_WEIGHT


def _workspace_tmp(name: str) -> Path:
    p = Path(__file__).resolve().parents[2] / ".lifecycle-test-tmp" / name
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_review_queue_lifecycle(monkeypatch):
    tmp_path = _workspace_tmp("queue")
    monkeypatch.setenv("BASIC_MEMORY_REVIEW_QUEUE", str(tmp_path / "queue.json"))
    q = ReviewQueue()
    added = q.propose([{
        "operation": "update", "permalink": "memory://lin",
        "metadata": {FIELD_WEIGHT: 2.0},
        "reason": "scene change",
        "evidence": [{"source": "ch1", "quote": "..."}],
    }])
    assert len(added) == 1
    assert len(q.propose([{
        "operation": "update", "permalink": "memory://lin",
        "metadata": {FIELD_WEIGHT: 2.0}, "reason": "scene change",
    }])) == 0
    assert q.list_pending()[0].hits == 2
    q.decide(added[0].id, False)
    assert q.list_pending() == []


def test_apply_patch_filters_to_lifecycle_fields(monkeypatch):
    tmp_path = _workspace_tmp("patch")
    monkeypatch.setenv("BASIC_MEMORY_REVIEW_QUEUE", str(tmp_path / "queue.json"))
    q = ReviewQueue()
    added = q.propose([{
        "operation": "status_progression", "permalink": "memory://lin",
        "metadata": {FIELD_CANON_STATUS: CANON_CANON, "tags": ["x"]},
        "reason": "confirmed",
    }])[0]
    patch = ReviewQueue.applies_to_metadata(added)
    assert patch == {FIELD_CANON_STATUS: CANON_CANON}


def test_search_payload_weight_sort(monkeypatch):
    monkeypatch.setenv("BASIC_MEMORY_LIFECYCLE", "1")
    payload = {"results": [
        {"file_path": "archived.md", "score": 0.9, "metadata": {FIELD_WEIGHT: 3, FIELD_CANON_STATUS: CANON_ARCHIVED}},
        {"file_path": "canon.md", "score": 0.7, "metadata": {FIELD_WEIGHT: 3, FIELD_CANON_STATUS: CANON_CANON}},
    ]}
    out = _apply_lifecycle_to_payload(payload, sort_by="weight", include_archived=False)
    assert len(out["results"]) == 1
    assert out["results"][0]["score"] == pytest.approx(0.7)
    assert out["total_is_exact"] is False


def test_search_payload_weight_sort_reorders_results(monkeypatch):
    monkeypatch.setenv("BASIC_MEMORY_LIFECYCLE", "1")
    payload = {"total": 2, "results": [
        {"file_path": "low.md", "score": 0.9, "metadata": {FIELD_WEIGHT: 1, FIELD_CANON_STATUS: CANON_CANON}},
        {"file_path": "high.md", "score": 0.7, "metadata": {FIELD_WEIGHT: 3, FIELD_CANON_STATUS: CANON_CANON}},
    ]}
    out = _apply_lifecycle_to_payload(payload, sort_by="weight", include_archived=True)
    assert [r["file_path"] for r in out["results"]] == ["high.md", "low.md"]
    assert out["results"][0]["score"] > out["results"][1]["score"]
    assert "total_is_exact" not in out


def test_lifecycle_disabled_is_noop(monkeypatch):
    monkeypatch.delenv("BASIC_MEMORY_LIFECYCLE", raising=False)
    payload = {"results": [{"score": 0.9, "metadata": {FIELD_CANON_STATUS: CANON_ARCHIVED}}]}
    out = _apply_lifecycle_to_payload(payload, sort_by="weight", include_archived=False)
    assert out is payload
    assert len(out["results"]) == 1
