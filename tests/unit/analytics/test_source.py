"""분석 입력 값 객체(`SourceSnapshot` 등)와 날짜 창, 거슬러 읽기(D2) 판단."""

from datetime import UTC, date, datetime, timedelta

import pytest

from mosemo.analytics.domain.day_reading import BackwardRead
from mosemo.analytics.domain.source import (
    CompletedFocusSession,
    ConfirmedLabel,
    DayWindow,
    InvalidSourceError,
    SegmentKind,
    SourceSegment,
    SourceSnapshot,
)
from mosemo.analytics.domain.usage_targets import UsageTargetResolver
from mosemo.shared.domain.timezones import Timezone, timeline_date_window
from tests.support.analytics_product import segment_of
from tests.support.analytics_strategies import PSL
from tests.support.analytics_timelines import OPAQUE, activity, app, gap

T0 = datetime(2026, 11, 3, tzinfo=UTC)


def minutes(value: int) -> datetime:
    return T0 + timedelta(minutes=value)


def snapshot(*segments: SourceSegment) -> SourceSnapshot:
    return SourceSnapshot(timezone=Timezone.UTC, segments=segments)


@pytest.mark.parametrize("timezone", list(Timezone))
@pytest.mark.parametrize(
    "day", [date(2026, 3, 8), date(2026, 11, 1), date(2026, 11, 3), date(1, 1, 1)]
)
def test_day_window_has_the_timeline_date_boundaries(
    timezone: Timezone, day: date
) -> None:
    expected = timeline_date_window(
        account_timezone=timezone, requested_date=day, now=minutes(0) + timedelta(1)
    )

    assert expected is not None
    window = DayWindow.of(timezone, day)
    assert (window.day, window.start, window.end) == expected


def test_day_window_is_half_open() -> None:
    window = DayWindow.of(Timezone.UTC, date(2026, 11, 3))

    assert window.contains(window.start)
    assert not window.contains(window.end)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"ended_at": minutes(5), "observed_end": minutes(4)}, "must equal"),
        ({"ended_at": None, "observed_end": None}, "last observation"),
        ({"ended_at": minutes(-1), "observed_end": minutes(-1)}, "before it starts"),
    ],
)
def test_segment_rejects_inconsistent_ends(fields: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SourceSegment(
            id="s1",
            first_event_id="e1",
            kind=SegmentKind.ACTIVITY,
            started_at=minutes(0),
            **fields,
        )


def test_snapshot_rejects_unordered_overlapping_or_inner_open_segments() -> None:
    first = segment_of(activity("s1", 0, 10, app("A")))
    overlapping = segment_of(activity("s2", 5, 15, app("B")))
    open_gap = segment_of(gap("g1", 10, None))

    with pytest.raises(InvalidSourceError, match="overlap"):
        snapshot(first, overlapping)
    with pytest.raises(InvalidSourceError, match="last one"):
        snapshot(open_gap, overlapping)
    assert snapshot(first, open_gap).segments == (first, open_gap)


def test_invalid_source_error_stays_a_value_error() -> None:
    """Callers that expect `ValueError` from a broken snapshot keep working."""
    first = segment_of(activity("s1", 0, 10, app("A")))
    overlapping = segment_of(activity("s2", 5, 15, app("B")))

    assert issubclass(InvalidSourceError, ValueError)
    with pytest.raises(ValueError, match="overlap"):
        snapshot(first, overlapping)


def test_zero_length_segments_at_one_instant_adjoin() -> None:
    first = segment_of(activity("s1", 0, 0, app("A")))
    second = segment_of(activity("s2", 0, 0, app("B")))

    assert first.adjoins(second)
    assert snapshot(first, second).segments == (first, second)


def test_only_a_valid_confirmation_of_detailed_activity_gives_a_label() -> None:
    current = segment_of(activity("s1", 0, 10, app("A")))
    stale = segment_of(activity("s2", 10, 20, app("A"), version="v2"))
    opaque = segment_of(activity("s3", 20, 30, OPAQUE))
    capture_gap = segment_of(gap("g1", 30, 40))
    source = SourceSnapshot(
        timezone=Timezone.UTC,
        segments=(current, stale, opaque, capture_gap),
        confirmations={
            segment.first_event_id: ConfirmedLabel(segment_version="v1", label_id="L")
            for segment in (current, stale, opaque, capture_gap)
        },
    )

    assert [source.label(s) for s in source.segments] == ["L", None, None, None]
    assert source.confirmation(opaque) is not None
    assert source.confirmation(capture_gap) is None


@pytest.mark.parametrize(
    ("pauses", "expected"),
    [
        (None, [(0, 60)]),
        ((), [(0, 60)]),
        (((10, 20),), [(0, 10), (20, 60)]),
        (((0, 10), (50, 60)), [(10, 50)]),
        (((30, 40), (10, 20)), [(0, 10), (20, 30), (40, 60)]),
    ],
)
def test_work_intervals_leave_out_pauses(
    pauses: tuple[tuple[int, int], ...] | None, expected: list[tuple[int, int]]
) -> None:
    session = CompletedFocusSession(
        id="f1",
        started_at=minutes(0),
        ended_at=minutes(60),
        label_id="L",
        pauses=None
        if pauses is None
        else tuple((minutes(a), minutes(b)) for a, b in pauses),
    )

    assert session.work_intervals() == tuple(
        (minutes(a), minutes(b)) for a, b in expected
    )
    assert session.has_pause_info is (pauses is not None)


def test_session_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="before it starts"):
        CompletedFocusSession(
            id="f1",
            started_at=minutes(10),
            ended_at=minutes(0),
            label_id="L",
            pauses=None,
        )


def test_backward_read_needs_more_while_the_page_continues_the_run() -> None:
    resolver = UsageTargetResolver(PSL)
    older = segment_of(activity("s0", -30, -20, app("B")))
    run = [
        segment_of(activity("s1", -20, -10, app("A", "1"))),
        segment_of(activity("s2", -10, 0, app("A", "2"))),
    ]
    first = segment_of(activity("s3", 0, 10, app("A", "3")))
    backward = BackwardRead(resolver, first=first)

    assert backward.needs_more([])
    assert backward.needs_more(run)
    assert backward.chain([older, *run]) == run
    assert not backward.needs_more([older, *run])


def test_backward_read_stops_at_a_silence_or_without_a_target() -> None:
    resolver = UsageTargetResolver(PSL)
    silent = segment_of(activity("s1", -20, -5, app("A")))
    first = segment_of(activity("s2", 0, 10, app("A")))
    unknown = segment_of(activity("s3", 0, 10, OPAQUE))

    assert not BackwardRead(resolver, first=first).needs_more([silent])
    assert not BackwardRead(resolver, first=unknown).needs_more([])
    assert BackwardRead(resolver, first=unknown).chain([silent]) == []
