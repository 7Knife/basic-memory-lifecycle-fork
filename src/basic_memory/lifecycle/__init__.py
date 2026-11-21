"""Generic memory lifecycle support for Basic Memory.

This package adds optional, non-novel-specific lifecycle metadata to notes:

- weight / base_weight / last_hit / last_decay_at / canon_status
- decay and archive rules
- hit bonus on retrieval
- review proposal queue with an explicit propose/decide/apply gate

Lifecycle helpers are pure functions; the MCP hit tool writes through the
Knowledge API `set_metadata` path. The unused `LifecycleService` uses
`EntityService.edit_entity_with_content` and is deprecated.
No SQL, SQLite tables, or Markdown rendering code are modified.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

FIELD_WEIGHT = "weight"
FIELD_BASE_WEIGHT = "base_weight"
FIELD_LAST_HIT = "last_hit"
FIELD_LAST_DECAY = "last_decay_at"
FIELD_CANON_STATUS = "canon_status"
FIELD_ARCHIVED_AT = "archived_at"
FIELD_SUPERSEDED = "superseded"

CANON_DRAFT = "draft"
CANON_PENDING = "pending"
CANON_CANON = "canon"
CANON_ARCHIVED = "archived"
CANON_OBSOLETE = "obsolete"
CANON_STATUSES = (
    CANON_DRAFT, CANON_PENDING, CANON_CANON, CANON_ARCHIVED, CANON_OBSOLETE
)

# Generic defaults; domain-specific presets can override via metadata.
DEFAULT_WEIGHTS: dict[str, float] = {
    "identity": 3.0, "agreement": 3.0, "emotion": 3.0,
    "preference": 2.5, "meme": 1.6, "misc": 1.3,
    "decision": 3.0, "canon": 3.0, "fact": 1.3,
}
FALLBACK_WEIGHT = 1.3

DECAY_PER_DAY = 0.1
DECAY_INTERVAL_DAYS = 3
HIT_BONUS = 0.5
HIT_CAP_FACTOR = 1.5
ARCHIVE_THRESHOLD = 1.0
MAX_WEIGHT = 3.0

LIFECYCLE_FIELDS = frozenset({
    FIELD_WEIGHT, FIELD_BASE_WEIGHT, FIELD_LAST_HIT, FIELD_LAST_DECAY,
    FIELD_CANON_STATUS, FIELD_ARCHIVED_AT, FIELD_SUPERSEDED,
})


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        raw = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _parse_float(value: object, default: float) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float) -> float:
    return max(0.0, min(MAX_WEIGHT, value))


def lifecycle_enabled() -> bool:
    """Lifecycle features are opt-in via BASIC_MEMORY_LIFECYCLE=1."""
    return os.environ.get("BASIC_MEMORY_LIFECYCLE", "").strip().lower() in {"1", "true", "yes", "on"}


def default_weight(category: str | None) -> float:
    if not category:
        return FALLBACK_WEIGHT
    return DEFAULT_WEIGHTS.get(str(category).strip().lower(), FALLBACK_WEIGHT)


def decay_update(
    metadata: dict[str, Any],
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """Compute the frontmatter update for one decay step. Pure function."""
    now = (now or _now()).replace(microsecond=0)
    status = str(metadata.get(FIELD_CANON_STATUS, "") or "")
    if status and status not in CANON_STATUSES:
        return None
    if status == CANON_ARCHIVED:
        return None

    weight = _parse_float(metadata.get(FIELD_WEIGHT), FALLBACK_WEIGHT)
    starts = [
        dt for dt in (
            _parse_dt(metadata.get(FIELD_LAST_HIT)),
            _parse_dt(metadata.get(FIELD_LAST_DECAY)),
        ) if dt is not None
    ]
    start = max(starts) if starts else now - timedelta(days=DECAY_INTERVAL_DAYS)
    days = max(0.0, (now - start).total_seconds() / 86400.0)

    if force:
        loss = DECAY_PER_DAY
    else:
        steps = int(days // DECAY_INTERVAL_DAYS)
        if steps < 1:
            return None
        loss = DECAY_PER_DAY * DECAY_INTERVAL_DAYS * steps

    new_weight = clamp(weight - loss)
    archived = new_weight < ARCHIVE_THRESHOLD
    update: dict[str, Any] = {
        FIELD_WEIGHT: round(new_weight, 3),
        FIELD_LAST_DECAY: _iso(now),
    }
    if FIELD_BASE_WEIGHT not in metadata or metadata.get(FIELD_BASE_WEIGHT) in (None, ""):
        update[FIELD_BASE_WEIGHT] = round(weight, 3)
    if archived:
        update[FIELD_CANON_STATUS] = CANON_ARCHIVED
        update[FIELD_ARCHIVED_AT] = _iso(now)
    return update


def hit_bonus_update(metadata: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
    """Compute the frontmatter update after a retrieval hit. Pure function."""
    now = (now or _now()).replace(microsecond=0)
    if str(metadata.get(FIELD_CANON_STATUS, "") or "") == CANON_ARCHIVED:
        return None
    weight = _parse_float(metadata.get(FIELD_WEIGHT), FALLBACK_WEIGHT)
    base = _parse_float(metadata.get(FIELD_BASE_WEIGHT), weight)
    cap = max(base * HIT_CAP_FACTOR, HIT_CAP_FACTOR)
    new_weight = clamp(min(weight + HIT_BONUS, cap))
    if new_weight == weight:
        return {FIELD_LAST_HIT: _iso(now)}
    return {FIELD_WEIGHT: round(new_weight, 3), FIELD_LAST_HIT: _iso(now)}


def rank_score(metadata: dict[str, Any], relevance: float) -> float:
    """Generic lifecycle-aware relevance: relevance * normalized weight * status factor."""
    if not lifecycle_enabled():
        return relevance
    weight = _parse_float(metadata.get(FIELD_WEIGHT), FALLBACK_WEIGHT)
    normalized = max(0.0, min(weight / MAX_WEIGHT, 1.0))
    status = str(metadata.get(FIELD_CANON_STATUS, "") or "")
    factor = {
        CANON_ARCHIVED: 0.2,
        CANON_DRAFT: 0.6,
        CANON_PENDING: 0.8,
    }.get(status, 1.0)
    return relevance * normalized * factor


def should_hide(metadata: dict[str, Any], include_archived: bool) -> bool:
    if include_archived:
        return False
    if not lifecycle_enabled():
        return False
    return str(metadata.get(FIELD_CANON_STATUS, "") or "") == CANON_ARCHIVED


# --- Review proposals ---------------------------------------------------

@dataclass
class Proposal:
    id: str
    operation: str  # new | update | status_progression | archive
    permalink: str | None
    metadata: dict[str, Any]
    reason: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    status: str = CANON_PENDING  # pending | approved | rejected | applied
    created_at: str = field(default_factory=lambda: _iso())
    decided_at: str | None = None
    hits: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "operation": self.operation, "permalink": self.permalink,
            "metadata": self.metadata, "reason": self.reason, "evidence": self.evidence,
            "status": self.status, "created_at": self.created_at,
            "decided_at": self.decided_at, "hits": self.hits,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "Proposal":
        return cls(
            id=str(row["id"]), operation=str(row["operation"]),
            permalink=row.get("permalink"), metadata=dict(row.get("metadata") or {}),
            reason=str(row.get("reason") or ""), evidence=list(row.get("evidence") or []),
            status=str(row.get("status") or CANON_PENDING),
            created_at=str(row.get("created_at") or _iso()),
            decided_at=row.get("decided_at"), hits=int(row.get("hits") or 1),
        )


class ReviewQueue:
    """JSON-file-backed proposal queue. Path is configurable via env."""

    def __init__(self, path: Path | None = None):
        self.path = path or Path(
            os.environ.get(
                "BASIC_MEMORY_REVIEW_QUEUE",
                str(Path.home() / ".basic-memory" / "review-queue.json"),
            )
        )

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def propose(self, proposals: Iterable[dict[str, Any]]) -> list[Proposal]:
        rows = self._load()
        added: list[Proposal] = []
        for item in proposals:
            operation = str(item.get("operation") or "")
            if operation not in {"new", "update", "status_progression", "archive"}:
                continue
            metadata = dict(item.get("metadata") or {})
            duplicate = next(
                (row for row in rows
                 if row.get("status") == CANON_PENDING
                 and row.get("operation") == operation
                 and row.get("permalink") == item.get("permalink")
                 and row.get("metadata") == metadata),
                None,
            )
            if duplicate is not None:
                duplicate["hits"] = int(duplicate.get("hits") or 1) + 1
                continue
            proposal = Proposal(
                id=str(item.get("id") or uuid.uuid4()),
                operation=operation,
                permalink=item.get("permalink"),
                metadata=metadata,
                reason=str(item.get("reason") or ""),
                evidence=list(item.get("evidence") or []),
            )
            rows.append(proposal.to_dict())
            added.append(proposal)
        self._save(rows)
        return added

    def list_pending(self) -> list[Proposal]:
        return [Proposal.from_dict(row) for row in self._load()
                if row.get("status") in {CANON_PENDING, CANON_DRAFT}]

    def counts(self) -> dict[str, int]:
        """Proposal counts grouped by status, for status reporting."""
        rows = self._load()
        counts: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or CANON_PENDING)
            counts[status] = counts.get(status, 0) + 1
        return counts

    def get(self, proposal_id: str) -> Proposal | None:
        for row in self._load():
            if row.get("id") == proposal_id:
                return Proposal.from_dict(row)
        return None

    def decide(self, proposal_id: str, approve: bool) -> Proposal | None:
        rows = self._load()
        for row in rows:
            if row.get("id") == proposal_id:
                row["status"] = "approved" if approve else "rejected"
                row["decided_at"] = _iso()
                self._save(rows)
                return Proposal.from_dict(row)
        return None

    def mark_applied(self, proposal_id: str) -> None:
        rows = self._load()
        for row in rows:
            if row.get("id") == proposal_id:
                row["status"] = "applied"
                self._save(rows)
                return

    @staticmethod
    def applies_to_metadata(proposal: Proposal) -> dict[str, Any]:
        """Metadata updates carried by a proposal (not a note-body rewrite)."""
        meta = dict(proposal.metadata)
        if proposal.operation == "archive":
            meta[FIELD_CANON_STATUS] = CANON_ARCHIVED
            meta[FIELD_ARCHIVED_AT] = _iso()
        elif proposal.operation == "status_progression":
            status = str(meta.get(FIELD_CANON_STATUS, ""))
            if status not in CANON_STATUSES:
                status = CANON_CANON
            meta[FIELD_CANON_STATUS] = status
        return {key: value for key, value in meta.items() if key in LIFECYCLE_FIELDS or key == FIELD_CANON_STATUS}


__all__ = [
    "FIELD_WEIGHT", "FIELD_BASE_WEIGHT", "FIELD_LAST_HIT", "FIELD_LAST_DECAY",
    "FIELD_CANON_STATUS", "FIELD_ARCHIVED_AT", "CANON_DRAFT", "CANON_PENDING",
    "CANON_CANON", "CANON_ARCHIVED", "CANON_OBSOLETE", "CANON_STATUSES",
    "LIFECYCLE_FIELDS", "lifecycle_enabled", "default_weight", "decay_update",
    "hit_bonus_update", "rank_score", "should_hide", "ReviewQueue", "Proposal",
]
