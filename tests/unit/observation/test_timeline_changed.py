from datetime import UTC, datetime, timedelta
from uuid import uuid4

from mosemo.observation.domain.events import TimelineChanged
from mosemo.observation.domain.timeline import (
    ActivityTimelineSegment,
    ProjectedActivity,
    ProjectedGap,
    SegmentType,
    timeline_changed,
)

T = datetime(2026, 9, 14, tzinfo=UTC)
ACCOUNT = uuid4()


def at(seconds: int) -> datetime:
    return T + timedelta(seconds=seconds)


def stored_activity(
    start: int, last: int, end: int | None, first_event=None
) -> ActivityTimelineSegment:
    return ActivityTimelineSegment(
        account_id=ACCOUNT,
        segment_type=SegmentType.ACTIVITY,
        started_at=at(start),
        ended_at=None if end is None else at(end),
        first_event_id=first_event or uuid4(),
        last_event_id=uuid4(),
        last_observed_at=at(last),
        context={"kind": "opaque"},
    )


def stored_gap(start: int) -> ActivityTimelineSegment:
    return ActivityTimelineSegment(
        account_id=ACCOUNT,
        segment_type=SegmentType.CAPTURE_GAP,
        started_at=at(start),
        first_event_id=uuid4(),
        reason="user_paused",
    )


def changed(start: int, end: int) -> TimelineChanged:
    return TimelineChanged(
        account_id=ACCOUNT, changed_from=at(start), changed_to=at(end)
    )


def test_same_segments_are_no_change() -> None:
    old = stored_activity(0, 10, 20)
    same = ProjectedActivity(
        started_at=old.started_at,
        first_event_id=old.first_event_id,
        last_event_id=old.last_event_id or uuid4(),
        last_observed_at=at(10),
        context={"kind": "opaque"},
        ended_at=at(20),
    )

    assert timeline_changed(ACCOUNT, removed=[old], added=[same]) is None
    assert timeline_changed(ACCOUNT, removed=[], added=[]) is None


def test_range_covers_removed_and_added_recorded_ends() -> None:
    # An open activity ends at its last observation, an open gap at its start.
    assert timeline_changed(
        ACCOUNT, removed=[stored_activity(0, 10, None)], added=[]
    ) == changed(0, 10)
    assert timeline_changed(
        ACCOUNT, removed=[], added=[ProjectedGap(at(5), uuid4(), "user_paused")]
    ) == changed(5, 5)
    assert timeline_changed(ACCOUNT, removed=[stored_gap(30)], added=[]) == changed(
        30, 30
    )
    # The removed segment may end later than every new one.
    assert timeline_changed(
        ACCOUNT,
        removed=[stored_activity(10, 50, 60)],
        added=[
            ProjectedActivity(
                started_at=at(10),
                first_event_id=uuid4(),
                last_event_id=uuid4(),
                last_observed_at=at(20),
                context={"kind": "opaque"},
                ended_at=at(25),
            )
        ],
    ) == changed(10, 60)
