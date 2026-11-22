"""Generic memory lifecycle support for Basic Memory.

This package adds optional, non-novel-specific lifecycle metadata to notes:

- weight / base_weight / last_hit / last_decay_at / canon_status
- decay and archive rules
- hit bonus on retrieval
- review proposal queue with an explicit propose/decide/apply gate

Lifecycle helpers are pure functions; the MCP hit and decay tools write through
the Knowledge API `set_metadata` path, which is the only write path this fork
has. No SQL, SQLite tables, or Markdown rendering code are modified.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
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
CANON_STATUSES = (CANON_DRAFT, CANON_PENDING, CANON_CANON, CANON_ARCHIVED, CANON_OBSOLETE)

# The weight a note carries when it states none. A note's own frontmatter is
# the only source of its weight; there is no per-type preset table, because a
# preset would silently override what the note says.
FALLBACK_WEIGHT = 1.3

DECAY_PER_DAY = 0.1
DECAY_INTERVAL_DAYS = 3
HIT_BONUS = 0.5
HIT_CAP_FACTOR = 1.5
ARCHIVE_THRESHOLD = 1.0
MAX_WEIGHT = 3.0

LIFECYCLE_FIELDS = frozenset(
    {
        FIELD_WEIGHT,
        FIELD_BASE_WEIGHT,
        FIELD_LAST_HIT,
        FIELD_LAST_DECAY,
        FIELD_CANON_STATUS,
        FIELD_ARCHIVED_AT,
        FIELD_SUPERSEDED,
    }
)

# Lifecycle fields that carry a number. Frontmatter parses every scalar to a
# string, so these arrive as text and are converted back where they are read.
LIFECYCLE_NUMERIC_FIELDS = frozenset({FIELD_WEIGHT, FIELD_BASE_WEIGHT})


def parse_lifecycle_number(value: object) -> float | None:
    """Read a lifecycle number, or None when the value does not spell one.

    Frontmatter stores numbers as text, so "3.0" is a number here while "heavy"
    is not. A value that does not spell a number is rejected rather than
    silently treated as zero, which would hide a note's real weight.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


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


def project_scope_key(project: str | None) -> str | None:
    """The key that identifies a project inside the review queue.

    A proposal records this key, and a queue scoped to a project only shows
    and accepts that project's proposals. Two projects that share a note
    permalink are then kept apart.

    The key is the project's name as a permalink, which both the main server
    and the standalone review server can compute from configuration alone —
    the standalone server deliberately runs without a database, so an id that
    only the database knows would not be available to both.
    """
    if project is None:
        return None
    candidate = str(project).strip()
    if not candidate:
        return None
    # A workspace-qualified name keeps its prefix, so two workspaces holding
    # a project of the same name stay apart.
    parts = [part for part in candidate.split("/") if part]
    if not parts:
        return None
    return "/".join(_slug_project_part(part) for part in parts)


def _slug_project_part(part: str) -> str:
    """One project's name as a stable, comparable key."""
    slug = part.strip().lower()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"[^a-z0-9\-.]", "", slug)
    return slug.strip("-") or part.strip().lower()


def lifecycle_enabled() -> bool:
    """Lifecycle features are opt-in via BASIC_MEMORY_LIFECYCLE=1."""
    return os.environ.get("BASIC_MEMORY_LIFECYCLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def decay_update(
    metadata: dict[str, Any],
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """Compute the frontmatter update for one decay step. Pure function.

    A note's weight falls by one step per `DECAY_INTERVAL_DAYS` since its
    last hit or decay. `last_decay_at` advances only by the whole steps that
    were charged, so the days left over stay on the timeline and are charged
    at the next call: how much a note decays depends on how long it has been
    unused, not on how often decay was run.

    `force` charges a single day's decay, and advances the timeline by that
    same day.
    """
    now = (now or _now()).replace(microsecond=0)
    status = str(metadata.get(FIELD_CANON_STATUS, "") or "")
    if status and status not in CANON_STATUSES:
        return None
    if status == CANON_ARCHIVED:
        return None

    weight = _parse_float(metadata.get(FIELD_WEIGHT), FALLBACK_WEIGHT)
    starts = [
        dt
        for dt in (
            _parse_dt(metadata.get(FIELD_LAST_HIT)),
            _parse_dt(metadata.get(FIELD_LAST_DECAY)),
        )
        if dt is not None
    ]
    start = max(starts) if starts else now - timedelta(days=DECAY_INTERVAL_DAYS)
    days = max(0.0, (now - start).total_seconds() / 86400.0)

    if force:
        loss = DECAY_PER_DAY
        advanced = timedelta(days=1)
    else:
        steps = int(days // DECAY_INTERVAL_DAYS)
        if steps < 1:
            return None
        loss = DECAY_PER_DAY * DECAY_INTERVAL_DAYS * steps
        advanced = timedelta(days=steps * DECAY_INTERVAL_DAYS)

    new_weight = clamp(weight - loss)
    archived = new_weight < ARCHIVE_THRESHOLD
    update: dict[str, Any] = {
        FIELD_WEIGHT: round(new_weight, 3),
        # The timeline moves by the steps that were charged, not to now: the
        # remainder is still owed.
        FIELD_LAST_DECAY: _iso(start + advanced),
    }
    if FIELD_BASE_WEIGHT not in metadata or metadata.get(FIELD_BASE_WEIGHT) in (None, ""):
        update[FIELD_BASE_WEIGHT] = round(weight, 3)
    if archived:
        update[FIELD_CANON_STATUS] = CANON_ARCHIVED
        update[FIELD_ARCHIVED_AT] = _iso(now)
    return update


def hit_bonus_update(
    metadata: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any] | None:
    """Compute the frontmatter update after a retrieval hit. Pure function.

    A hit raises the note's weight by one bonus, up to a cap of
    `base_weight * HIT_CAP_FACTOR`. The base is the weight the note had when
    it was first hit, and it is written then: without it the cap would be
    measured against the note's current weight, and every hit would raise the
    ceiling it is measured against, so the weight would climb without limit.
    The effective ceiling is the smaller of that cap and `MAX_WEIGHT`.
    """
    now = (now or _now()).replace(microsecond=0)
    if str(metadata.get(FIELD_CANON_STATUS, "") or "") == CANON_ARCHIVED:
        return None
    weight = _parse_float(metadata.get(FIELD_WEIGHT), FALLBACK_WEIGHT)
    base = _parse_float(metadata.get(FIELD_BASE_WEIGHT), 0.0)
    seeded = base <= 0.0
    if seeded:
        # The first hit fixes the base at the note's current weight, so the
        # cap stays where it is from then on.
        base = weight
    cap = base * HIT_CAP_FACTOR
    new_weight = clamp(min(weight + HIT_BONUS, cap))
    update: dict[str, Any] = {FIELD_LAST_HIT: _iso(now)}
    if seeded:
        update[FIELD_BASE_WEIGHT] = round(base, 3)
    if new_weight != weight:
        update[FIELD_WEIGHT] = round(new_weight, 3)
    return update


def rank_score(metadata: dict[str, Any], relevance: float) -> float:
    """Generic lifecycle-aware relevance: relevance * normalized weight * status factor.

    `relevance` is a magnitude, not a signed value: SQLite's bm25 returns negative
    scores and Postgres' ts_rank positive ones, and the weight factor is a positive
    multiplier. Multiplying a negative score by it would invert the order the
    weight is meant to express, so the magnitude is what is scaled.
    """
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
    return abs(relevance) * normalized * factor


def should_hide(metadata: dict[str, Any], include_archived: bool) -> bool:
    if include_archived:
        return False
    if not lifecycle_enabled():
        return False
    return str(metadata.get(FIELD_CANON_STATUS, "") or "") == CANON_ARCHIVED


# --- Review proposals ---------------------------------------------------


class ReviewQueueError(Exception):
    """Base class for review queue failures."""


class ReviewQueueCorruptError(ReviewQueueError):
    """The queue file exists but does not hold a readable proposal list.

    Raised instead of reading a broken file as an empty queue: an empty read
    would make the next write overwrite the file, destroying the proposals it
    still holds.
    """

    def __init__(self, path: Path, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Review queue at '{path}' is unreadable: {reason}")


class ProposalProjectError(ReviewQueueError):
    """A proposal belongs to a different project than the one acting on it."""

    def __init__(self, proposal_id: str, owner: str | None, requested: str | None):
        self.proposal_id = proposal_id
        self.owner = owner
        self.requested = requested
        super().__init__(
            f"Proposal '{proposal_id}' belongs to project '{owner}', not '{requested}'."
        )


class ProposalStateError(ReviewQueueError):
    """A proposal is not in a state that allows the requested transition."""

    def __init__(self, proposal_id: str, current_status: str):
        self.proposal_id = proposal_id
        self.current_status = current_status
        super().__init__(
            f"Proposal '{proposal_id}' is already '{current_status}' and cannot be decided."
        )


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
    # The project that owns this proposal, as its scope key (see
    # `project_scope_key`). A proposal belongs to exactly one project: it is
    # only visible to, and only decidable by, that project. A proposal written
    # before this field existed has no owner and is refused rather than
    # guessed at.
    project: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "operation": self.operation,
            "permalink": self.permalink,
            "metadata": self.metadata,
            "reason": self.reason,
            "evidence": self.evidence,
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "hits": self.hits,
            "project": self.project,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "Proposal":
        return cls(
            id=str(row["id"]),
            operation=str(row["operation"]),
            permalink=row.get("permalink"),
            metadata=dict(row.get("metadata") or {}),
            reason=str(row.get("reason") or ""),
            evidence=list(row.get("evidence") or []),
            status=str(row.get("status") or CANON_PENDING),
            created_at=str(row.get("created_at") or _iso()),
            decided_at=row.get("decided_at"),
            hits=int(row.get("hits") or 1),
            project=row.get("project"),
        )


def _try_lock_file_handle(handle: int) -> None:
    """Try to take an exclusive advisory lock on an open file, without blocking.

    Windows and POSIX spell the lock differently, and neither is a busy-wait:
    a caller that cannot take the lock is told so at once.
    """
    if os.name == "nt":
        import msvcrt

        # msvcrt locks from the current position, so it starts at the beginning.
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file_handle(handle: int) -> None:
    """Release the advisory lock taken by `_try_lock_file_handle`."""
    if os.name == "nt":
        import msvcrt

        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_UN)


# How long a writer waits for the queue lock before giving up. The wait is
# bounded so a queue wedged by another process cannot hang every caller, and
# it is not zero so two processes writing at once do not lose a proposal.
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.02


class ReviewQueue:
    """JSON-file-backed proposal queue. Path is configurable via env.

    Proposals belong to the project that proposed them: `list_pending`,
    `counts` and `get` only report a project's own proposals, and `decide`
    refuses one owned by another project. The owner is recorded on the
    proposal when it is written.

    The file is guarded by an advisory lock, because the main MCP server and
    the standalone review server are separate processes that read and write
    the same queue.
    """

    def __init__(self, path: Path | None = None, *, project: str | None = None):
        self.path = path or Path(
            os.environ.get(
                "BASIC_MEMORY_REVIEW_QUEUE",
                str(Path.home() / ".basic-memory" / "review-queue.json"),
            )
        )
        # The project whose proposals this queue reads and writes. None means
        # an unscoped queue, which sees every proposal.
        self.project = project

    @property
    def lock_path(self) -> Path:
        """The lock file guarding the queue file."""
        return self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold the queue's advisory lock for the length of the block.

        The wait for the lock is bounded, so two processes writing at once do
        not lose a proposal, while a queue wedged by a process that never
        releases the lock cannot hang every caller: past the timeout the
        writer is told the queue is locked rather than waiting forever.
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    _try_lock_file_handle(handle)
                    break
                except OSError as error:
                    if time.monotonic() >= deadline:
                        raise ReviewQueueError(
                            f"Review queue at '{self.path}' is locked by another process: {error}"
                        ) from error
                    time.sleep(LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                _unlock_file_handle(handle)
        finally:
            os.close(handle)

    def _load(self) -> list[dict[str, Any]]:
        """Read the queue file.

        A file that is missing is an empty queue, which is the normal state
        before the first proposal. A file that exists but cannot be read as a
        list of proposals raises: reading it as empty would let the next write
        overwrite the proposals it still holds.
        """
        if not self.path.exists():
            return []
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as error:
            raise ReviewQueueCorruptError(self.path, f"cannot be read: {error}") from error
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ReviewQueueCorruptError(self.path, f"is not valid JSON: {error}") from error
        if not isinstance(data, list):
            raise ReviewQueueCorruptError(
                self.path, f"does not hold a list of proposals (found {type(data).__name__})"
            )
        return data

    def _save(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _visible_to_project(self, row: dict[str, Any]) -> bool:
        """Whether a stored row belongs to the project this queue is scoped to.

        An unscoped queue (no project) sees every row. A scoped queue sees
        only its own project's rows; a row with no owner belongs to no
        project, so no scoped queue claims it.
        """
        if self.project is None:
            return True
        return row.get("project") == self.project

    def propose(self, proposals: Iterable[dict[str, Any]]) -> list[Proposal]:
        """Queue new proposals, owned by this queue's project.

        A proposal identical to a pending one of the same project counts as
        another hit on it rather than a second proposal.
        """
        with self._locked():
            rows = self._load()
            added: list[Proposal] = []
            for item in proposals:
                operation = str(item.get("operation") or "")
                if operation not in {"new", "update", "status_progression", "archive"}:
                    continue
                metadata = dict(item.get("metadata") or {})
                duplicate = next(
                    (
                        row
                        for row in rows
                        if row.get("status") == CANON_PENDING
                        and row.get("operation") == operation
                        and row.get("permalink") == item.get("permalink")
                        and row.get("metadata") == metadata
                        and row.get("project") == self.project
                    ),
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
                    project=self.project,
                )
                rows.append(proposal.to_dict())
                added.append(proposal)
            self._save(rows)
            return added

    def list_pending(self) -> list[Proposal]:
        """Pending proposals of this queue's project."""
        return [
            Proposal.from_dict(row)
            for row in self._load()
            if row.get("status") == CANON_PENDING and self._visible_to_project(row)
        ]

    def counts(self) -> dict[str, int]:
        """Proposal counts grouped by status, for status reporting."""
        counts: dict[str, int] = {}
        for row in self._load():
            if not self._visible_to_project(row):
                continue
            status = str(row.get("status") or CANON_PENDING)
            counts[status] = counts.get(status, 0) + 1
        return counts

    def get(self, proposal_id: str) -> Proposal | None:
        """One proposal of this queue's project, or None if it has none.

        A queue with no project owns no proposals, so it finds none: an
        unscoped queue is a way to look at the whole queue, not a way to act
        on it.
        """
        if self.project is None:
            return None
        for row in self._load():
            if row.get("id") == proposal_id and self._visible_to_project(row):
                return Proposal.from_dict(row)
        return None

    def require_project(self) -> None:
        """Refuse to act on the queue without knowing which project it is.

        Deciding or applying a proposal changes what a later reader sees, so
        the caller has to say which project's proposal it is acting on. An
        unscoped queue can look at the whole queue, but not change it.
        """
        if self.project is None:
            raise ReviewQueueError(
                "Name the project to act on: a proposal is applied to the notes of "
                "one project, and an unscoped queue cannot say which."
            )

    def decide(self, proposal_id: str, approve: bool) -> Proposal | None:
        """Approve or reject a pending proposal of this queue's project.

        A proposal that another project owns is refused, and so is one that
        has already been decided: a late call must not reopen it.
        """
        self.require_project()
        with self._locked():
            rows = self._load()
            for row in rows:
                if row.get("id") != proposal_id:
                    continue
                owner = row.get("project")
                if owner != self.project:
                    raise ProposalProjectError(proposal_id, owner, self.project)
                status = str(row.get("status") or CANON_PENDING)
                if status != CANON_PENDING:
                    raise ProposalStateError(proposal_id, status)
                row["status"] = "approved" if approve else "rejected"
                row["decided_at"] = _iso()
                self._save(rows)
                return Proposal.from_dict(row)
        return None

    def mark_applied(self, proposal_id: str) -> None:
        """Record that a proposal's patch has been applied to a note."""
        self.require_project()
        with self._locked():
            rows = self._load()
            for row in rows:
                if row.get("id") == proposal_id:
                    owner = row.get("project")
                    if owner != self.project:
                        raise ProposalProjectError(proposal_id, owner, self.project)
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
        return {
            key: value
            for key, value in meta.items()
            if key in LIFECYCLE_FIELDS or key == FIELD_CANON_STATUS
        }


__all__ = [
    "FIELD_WEIGHT",
    "FIELD_BASE_WEIGHT",
    "FIELD_LAST_HIT",
    "FIELD_LAST_DECAY",
    "FIELD_CANON_STATUS",
    "FIELD_ARCHIVED_AT",
    "CANON_DRAFT",
    "CANON_PENDING",
    "CANON_CANON",
    "CANON_ARCHIVED",
    "CANON_OBSOLETE",
    "CANON_STATUSES",
    "LIFECYCLE_FIELDS",
    "LIFECYCLE_NUMERIC_FIELDS",
    "parse_lifecycle_number",
    "project_scope_key",
    "lifecycle_enabled",
    "decay_update",
    "hit_bonus_update",
    "rank_score",
    "should_hide",
    "ReviewQueue",
    "Proposal",
    "ReviewQueueError",
    "ReviewQueueCorruptError",
    "ProposalProjectError",
    "ProposalStateError",
]
