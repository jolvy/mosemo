from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mosemo.observation.domain.activities import MAX_OBSERVATION_GAP
from mosemo.observation.domain.timeline import (
    ActivityTimelineSegment,
    ProjectedActivity,
    ProjectedGap,
    ProjectionError,
    SegmentType,
)

START = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
CONTEXT: dict[str, object] = {"kind": "opaque"}


def activity(*, ended_at=None, context=None):
    event_id = uuid4()
    return ProjectedActivity(
        started_at=START,
        first_event_id=event_id,
        last_event_id=event_id,
        last_observed_at=START + timedelta(seconds=30),
        context=context or CONTEXT,
        ended_at=ended_at,
    )


def gap(*, ended_at=None):
    return ProjectedGap(
        started_at=START, first_event_id=uuid4(), reason="locked", ended_at=ended_at
    )


def stored(projected, **changes):
    segment = projected.to_segment(account_id=uuid4(), segment_id=uuid4())
    for name, value in changes.items():
        setattr(segment, name, value)
    return segment


@pytest.mark.parametrize("factory", [activity, gap])
def test_same_as_matches_only_identical_stored_segment(factory):
    projected = factory(ended_at=START + timedelta(minutes=1))

    assert projected.same_as(stored(projected))
    assert not projected.same_as(stored(projected, ended_at=None))
    assert not projected.same_as(stored(projected, first_event_id=uuid4()))


def test_same_as_rejects_other_segment_type():
    projected = gap()

    assert not projected.same_as(stored(projected, segment_type=SegmentType.ACTIVITY))


@pytest.mark.parametrize("factory", [activity, gap])
def test_converged_index_returns_index_of_same_segment(factory):
    projected = factory(ended_at=START + timedelta(minutes=1))
    existing = [stored(factory()), stored(projected)]

    assert projected.converged_index(existing, observed_at=START) == 1


@pytest.mark.parametrize("factory", [activity, gap])
def test_converged_index_ignores_segment_ending_before_event(factory):
    projected = factory(ended_at=START + timedelta(minutes=1))

    assert (
        projected.converged_index(
            [stored(projected)], observed_at=START + timedelta(minutes=2)
        )
        is None
    )


def test_continues_accepts_other_first_event_within_range():
    projected = activity(ended_at=START + timedelta(minutes=1))
    candidate = stored(
        activity(), first_event_id=uuid4(), started_at=START + timedelta(seconds=10)
    )

    assert projected.continues(candidate)


def test_continues_rejects_other_context_or_out_of_range_start():
    projected = activity(ended_at=START + timedelta(minutes=1))

    assert not projected.continues(stored(activity(context={"kind": "other"})))
    assert not projected.continues(
        stored(activity(), started_at=START + timedelta(minutes=2))
    )


def test_gap_continues_only_capture_gap():
    projected = gap()

    assert projected.continues(stored(gap(), started_at=START + timedelta(hours=1)))
    assert not projected.continues(stored(activity()))


def test_can_inherit_rejects_other_segment_type():
    assert activity().can_inherit(stored(activity()))
    assert not activity().can_inherit(stored(gap()))
    assert not gap().can_inherit(stored(activity()))


@pytest.mark.parametrize("factory", [activity, gap])
def test_to_segment_without_id_leaves_id_to_model_default(factory):
    first = factory().to_segment(account_id=uuid4(), segment_id=None)
    second = factory().to_segment(account_id=uuid4(), segment_id=None)

    assert isinstance(first, ActivityTimelineSegment)
    # The entity default (uuid7) assigns a fresh ID on construction (ADR 0013).
    assert first.id != second.id


def test_close_at_within_gap_ends_at_observed_time():
    projected = activity()
    observed_at = projected.last_observed_at + MAX_OBSERVATION_GAP

    projected.close_at(observed_at)

    assert projected.ended_at == observed_at


def test_close_at_beyond_gap_ends_at_last_observation():
    projected = activity()

    projected.close_at(
        projected.last_observed_at + MAX_OBSERVATION_GAP + timedelta(seconds=1)
    )

    assert projected.ended_at == projected.last_observed_at


def test_close_at_rejects_time_regression():
    projected = activity()

    with pytest.raises(ProjectionError, match="observation_time_regression"):
        projected.close_at(projected.last_observed_at - timedelta(seconds=1))
    assert projected.ended_at is None
