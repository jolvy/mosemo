from datetime import UTC, datetime, timedelta
from uuid import uuid4

from mosemo.activities.constants import MAX_OBSERVATION_GAP
from mosemo.activities.enums import ActivityTimelineKind, SegmentType
from mosemo.activities.models import ActivityTimelineSegment


def activity_segment(
    *,
    started_at: datetime,
    ended_at: datetime | None = None,
    last_observed_at: datetime | None = None,
    context_kind: str = "detailed",
) -> ActivityTimelineSegment:
    return ActivityTimelineSegment(
        account_id=uuid4(),
        segment_type=SegmentType.ACTIVITY,
        started_at=started_at,
        ended_at=ended_at,
        first_event_id=uuid4(),
        last_event_id=uuid4(),
        last_observed_at=last_observed_at or ended_at or started_at,
        context={"kind": context_kind},
    )


def test_activity_segment_ends_only_after_observation_gap() -> None:
    observed_at = datetime(2026, 9, 14, tzinfo=UTC)
    segment = activity_segment(
        started_at=observed_at,
        last_observed_at=observed_at,
    )

    assert segment.effective_ended_at(observed_at + MAX_OBSERVATION_GAP) is None
    assert (
        segment.effective_ended_at(
            observed_at + MAX_OBSERVATION_GAP + timedelta(microseconds=1)
        )
        == observed_at
    )
    assert segment.ended_at is None


def test_timeline_kind_distinguishes_gap_opaque_open_and_closed() -> None:
    observed_at = datetime(2026, 9, 14, tzinfo=UTC)
    now = observed_at + MAX_OBSERVATION_GAP + timedelta(seconds=1)
    open_detailed = activity_segment(
        started_at=observed_at,
        last_observed_at=observed_at,
    )
    closed_detailed = activity_segment(
        started_at=observed_at,
        ended_at=observed_at + timedelta(minutes=1),
    )
    opaque = activity_segment(
        started_at=observed_at,
        last_observed_at=observed_at,
        context_kind="opaque",
    )
    gap = ActivityTimelineSegment(
        account_id=uuid4(),
        segment_type=SegmentType.CAPTURE_GAP,
        started_at=observed_at,
        ended_at=None,
        first_event_id=uuid4(),
        last_event_id=None,
        last_observed_at=None,
        context=None,
        reason="screen_locked",
    )

    assert open_detailed.timeline_kind_at(observed_at) is (
        ActivityTimelineKind.IN_PROGRESS_ACTIVITY
    )
    assert closed_detailed.timeline_kind_at(now) is (
        ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY
    )
    assert opaque.timeline_kind_at(now) is ActivityTimelineKind.OPAQUE_ACTIVITY
    assert gap.timeline_kind_at(now) is ActivityTimelineKind.CAPTURE_GAP


def test_overlaps_window_handles_boundaries_and_zero_duration() -> None:
    start = datetime(2026, 9, 14, tzinfo=UTC)
    end = start + timedelta(days=1)
    now = end + timedelta(days=1)
    at_start = activity_segment(started_at=start, ended_at=start)
    at_end = activity_segment(started_at=end, ended_at=end)
    ends_at_start = activity_segment(
        started_at=start - timedelta(minutes=1),
        ended_at=start,
    )
    overlaps_start = activity_segment(
        started_at=start - timedelta(minutes=1),
        ended_at=start + timedelta(microseconds=1),
    )
    open_at_start = activity_segment(
        started_at=start - timedelta(minutes=1),
        last_observed_at=start,
    )

    assert at_start.overlaps_window(start=start, end=end, now=now)
    assert not at_end.overlaps_window(start=start, end=end, now=now)
    assert not ends_at_start.overlaps_window(start=start, end=end, now=now)
    assert overlaps_start.overlaps_window(start=start, end=end, now=now)
    assert open_at_start.overlaps_window(start=start, end=end, now=start)
