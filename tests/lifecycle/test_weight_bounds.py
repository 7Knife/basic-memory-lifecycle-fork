"""Weight cap and decay timeline semantics (review findings 7 and 8).

A hit must not raise the ceiling it is measured against, and decay must
charge a note for the time it has been unused rather than for the number of
times decay was run.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from basic_memory.lifecycle import (
    DECAY_INTERVAL_DAYS,
    DECAY_PER_DAY,
    FIELD_BASE_WEIGHT,
    FIELD_LAST_DECAY,
    FIELD_LAST_HIT,
    FIELD_WEIGHT,
    MAX_WEIGHT,
    decay_update,
    hit_bonus_update,
)

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)


def _meta(**overrides):
    meta = {
        FIELD_WEIGHT: 3.0,
        FIELD_BASE_WEIGHT: 3.0,
        FIELD_LAST_HIT: "2026-11-16T00:00:00Z",
    }
    meta.update(overrides)
    return meta


# --- Finding 7: the hit cap ---


def test_first_hit_seeds_the_base_weight():
    """The first hit fixes the base the cap is measured against."""
    update = hit_bonus_update({FIELD_WEIGHT: 2.0}, now=NOW)

    assert update is not None
    assert update[FIELD_BASE_WEIGHT] == 2.0
    assert update[FIELD_WEIGHT] == pytest.approx(2.5)


def test_repeated_hits_do_not_raise_the_ceiling():
    """A note's weight converges instead of climbing with every hit."""
    metadata = {FIELD_WEIGHT: 2.0}

    for _ in range(6):
        update = hit_bonus_update(metadata, now=NOW)
        assert update is not None
        metadata = {**metadata, **update}

    # The cap is the base (2.0) times 1.5.
    assert metadata[FIELD_WEIGHT] == pytest.approx(3.0)
    assert metadata[FIELD_BASE_WEIGHT] == pytest.approx(2.0)


def test_a_low_base_weight_cannot_be_tripled():
    """A note with a low base stays near its base rather than jumping."""
    update = hit_bonus_update({FIELD_WEIGHT: 0.5}, now=NOW)

    assert update is not None
    assert update[FIELD_WEIGHT] == pytest.approx(0.75)
    assert update[FIELD_BASE_WEIGHT] == pytest.approx(0.5)


def test_the_cap_never_exceeds_the_maximum_weight():
    """A high base cannot lift the weight past the maximum."""
    update = hit_bonus_update({FIELD_WEIGHT: 2.9}, now=NOW)

    assert update is not None
    assert update[FIELD_WEIGHT] == pytest.approx(MAX_WEIGHT)


def test_a_hit_at_the_cap_refreshes_only_the_timestamp():
    """A note already at its cap records the hit without moving its weight."""
    update = hit_bonus_update(_meta(weight=3.0, base_weight=3.0), now=NOW)

    assert update is not None
    assert FIELD_WEIGHT not in update
    assert update[FIELD_LAST_HIT] == "2026-11-20T00:00:00Z"


def test_an_archived_note_is_not_hit():
    assert hit_bonus_update({FIELD_WEIGHT: 2.0, "canon_status": "archived"}, now=NOW) is None


# --- Finding 8: the decay timeline ---


def test_decay_keeps_the_days_it_did_not_charge():
    """A remainder stays on the timeline, so a later call still charges it."""
    metadata = {
        FIELD_WEIGHT: 3.0,
        FIELD_BASE_WEIGHT: 3.0,
        FIELD_LAST_HIT: "2026-11-01T00:00:00Z",
    }

    # Five days after the hit, one whole step is charged.
    update = decay_update(metadata, now=datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert update is not None
    assert update[FIELD_WEIGHT] == pytest.approx(3.0 - DECAY_PER_DAY * DECAY_INTERVAL_DAYS)
    # The timeline advanced by the step that was charged, not to now.
    assert update[FIELD_LAST_DECAY] == "2026-11-04T00:00:00Z"

    # Two days later the remainder has become a second step.
    metadata = {**metadata, **update}
    later = decay_update(metadata, now=datetime(2026, 11, 8, tzinfo=timezone.utc))
    assert later is not None
    assert later[FIELD_WEIGHT] == pytest.approx(3.0 - 2 * DECAY_PER_DAY * DECAY_INTERVAL_DAYS)


def test_decay_does_not_depend_on_how_often_it_runs():
    """One long gap and several short ones cost the same."""
    one_call = decay_update(
        {
            FIELD_WEIGHT: 3.0,
            FIELD_BASE_WEIGHT: 3.0,
            FIELD_LAST_HIT: "2026-11-01T00:00:00Z",
        },
        now=datetime(2026, 11, 15, tzinfo=timezone.utc),
    )
    assert one_call is not None

    metadata = {
        FIELD_WEIGHT: 3.0,
        FIELD_BASE_WEIGHT: 3.0,
        FIELD_LAST_HIT: "2026-11-01T00:00:00Z",
    }
    for day in (4, 7, 10, 13):
        update = decay_update(metadata, now=datetime(2026, 11, day, tzinfo=timezone.utc))
        if update is not None:
            metadata = {**metadata, **update}

    assert metadata[FIELD_WEIGHT] == pytest.approx(one_call[FIELD_WEIGHT])


def test_decay_is_idempotent_within_an_interval():
    """A second call before the next step is due changes nothing."""
    metadata = {
        FIELD_WEIGHT: 3.0,
        FIELD_BASE_WEIGHT: 3.0,
        FIELD_LAST_HIT: "2026-11-16T00:00:00Z",
    }
    first = decay_update(metadata, now=NOW)
    assert first is not None

    again = decay_update({**metadata, **first}, now=NOW)
    assert again is None


def test_force_charges_one_day_and_advances_one_day():
    """A forced decay charges a single day and moves the timeline by that day."""
    metadata = {
        FIELD_WEIGHT: 3.0,
        FIELD_BASE_WEIGHT: 3.0,
        FIELD_LAST_HIT: "2026-11-19T00:00:00Z",
    }
    update = decay_update(metadata, now=NOW, force=True)

    assert update is not None
    assert update[FIELD_WEIGHT] == pytest.approx(3.0 - DECAY_PER_DAY)
    assert update[FIELD_LAST_DECAY] == "2026-11-20T00:00:00Z"


def test_a_hit_resets_the_decay_start():
    """A note used recently starts decaying from that use."""
    metadata = {
        FIELD_WEIGHT: 3.0,
        FIELD_BASE_WEIGHT: 3.0,
        FIELD_LAST_HIT: "2026-11-19T00:00:00Z",
        FIELD_LAST_DECAY: "2026-11-01T00:00:00Z",
    }
    # Only one day has passed since the hit, so no whole step is due.
    assert decay_update(metadata, now=NOW) is None
