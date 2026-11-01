from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mosemo.activities.constants import MAX_OBSERVATION_GAP
from mosemo.activities.enums import ActivityTimelineKind, RecordType, SegmentType
from mosemo.activities.models import ActivityRecord, ActivityTimelineSegment
from mosemo.timezones import Timezone


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


def test_timeline_kind_rejects_unknown_context_kind() -> None:
    observed_at = datetime(2026, 9, 14, tzinfo=UTC)
    segment = activity_segment(started_at=observed_at, context_kind="unknown")

    with pytest.raises(ValueError, match="unsupported activity context kind"):
        segment.timeline_kind_at(observed_at)


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


OBSERVED_AT = datetime(2026, 9, 14, tzinfo=UTC)
EVENT_ID = uuid4()
DEVICE_ID = uuid4()


def activity_record(**changes: object) -> ActivityRecord:
    values: dict[str, object] = {
        "event_id": EVENT_ID,
        "device_id": DEVICE_ID,
        "focus_session_id": None,
        "sequence": 3,
        "record_type": RecordType.ACTIVITY_OBSERVATION,
        "observed_at": OBSERVED_AT,
        "timezone_id": Timezone.ASIA_SEOUL,
        "utc_offset_minutes": 540,
        "payload": {"context": {"kind": "opaque"}},
        "received_at": OBSERVED_AT,
        **changes,
    }
    return ActivityRecord(**values)


@pytest.mark.parametrize(
    ("field", "different_value"),
    [
        ("event_id", uuid4()),
        ("device_id", uuid4()),
        ("focus_session_id", uuid4()),
        ("sequence", 99),
        ("record_type", RecordType.COLLECTION_STATE_CHANGED),
        ("observed_at", OBSERVED_AT + timedelta(hours=2)),
        ("timezone_id", Timezone.UTC),
        ("utc_offset_minutes", 0),
        ("payload", {"context": {"kind": "opaque", "unexpected": True}}),
    ],
)
def test_has_same_content_compares_every_stored_request_field(
    field: str, different_value: object
) -> None:
    assert not activity_record().has_same_content(
        activity_record(**{field: different_value})
    )


def test_has_same_content_ignores_received_at() -> None:
    assert activity_record().has_same_content(
        activity_record(received_at=OBSERVED_AT + timedelta(days=1))
    )


@pytest.mark.parametrize(
    ("previous", "following", "expected"),
    [
        (None, None, True),
        (OBSERVED_AT, OBSERVED_AT, True),
        (OBSERVED_AT - timedelta(seconds=1), OBSERVED_AT + timedelta(seconds=1), True),
        (OBSERVED_AT + timedelta(microseconds=1), None, False),
        (None, OBSERVED_AT - timedelta(microseconds=1), False),
    ],
    ids=["no-neighbors", "same-time", "ordered", "before-previous", "after-next"],
)
def test_is_in_device_order_rejects_only_neighbor_regression(
    previous: datetime | None, following: datetime | None, *, expected: bool
) -> None:
    assert (
        activity_record().is_in_device_order(previous=previous, following=following)
        is expected
    )
