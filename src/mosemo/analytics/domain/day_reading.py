"""Which segments a day's calculation reads (metrics spec D1, D2, P7 reads)."""

from collections.abc import Sequence

from mosemo.analytics.domain.source import (
    CompletedFocusSession,
    DayWindow,
    Interval,
    SourceSegment,
)
from mosemo.analytics.domain.usage_targets import UsageTargetResolver


def is_read(segment: SourceSegment, window: Interval) -> bool:
    """D1: `started_at < end AND COALESCE(ended_at, last_observed_at) >= start`.

    A capture gap without an end is read on every date from its start on.
    """
    start, end = window
    return segment.started_at < end and (
        segment.observed_end is None or segment.observed_end >= start
    )


class BackwardRead:
    """D2: read back from `first` through adjoining segments of the same target.

    `first` is the first closed segment the D1 condition chose. A page is the
    segments read so far before `first`, in start order.
    """

    def __init__(self, resolver: UsageTargetResolver, *, first: SourceSegment) -> None:
        self.resolver = resolver
        self.first = first

    def chain(self, page: Sequence[SourceSegment]) -> list[SourceSegment]:
        """The tail of `page` that adjoins `first` with the same target."""
        target = self.resolver.target(self.first)
        if target is None:
            return []
        chained: list[SourceSegment] = []
        after = self.first
        for before in reversed(page):
            if not before.adjoins(after) or self.resolver.target(before) != target:
                break
            chained.append(before)
            after = before
        return chained[::-1]

    def needs_more(self, page: Sequence[SourceSegment]) -> bool:
        """The whole page continues the run, so an earlier segment may continue it."""
        if self.resolver.target(self.first) is None:
            return False
        return len(self.chain(page)) == len(page)


def window_positions(
    segments: Sequence[SourceSegment],
    *,
    window: Interval,
    resolver: UsageTargetResolver,
) -> set[int]:
    """D1 rows of one window plus the D2 backward read from its first closed row."""
    chosen = {
        index for index, segment in enumerate(segments) if is_read(segment, window)
    }
    # An endless capture gap is read even when it starts earlier, so it is no anchor.
    closed = [
        index for index in sorted(chosen) if segments[index].observed_end is not None
    ]
    if closed:
        first = closed[0]
        backward = BackwardRead(resolver, first=segments[first])
        chained = len(backward.chain(segments[:first]))
        chosen.update(range(first - chained, first))
    return chosen


def ends_inside(segment: SourceSegment, window: DayWindow) -> bool:
    """A closed activity whose end is inside the date: a silence may follow it."""
    return (
        segment.is_activity
        and segment.ended_at is not None
        and window.contains(segment.ended_at)
    )


def select_day_segments(
    segments: Sequence[SourceSegment],
    *,
    sessions: Sequence[CompletedFocusSession],
    window: DayWindow,
    resolver: UsageTargetResolver,
) -> list[SourceSegment]:
    """The segments a date's calculation reads, in start order.

    The date window and the window of each completed session that started on the
    date each read D1 rows plus the D2 backward read. The segment after a closed
    activity ending on the date is read for the silence count (P7-B15).
    """
    windows: list[Interval] = [window.bounds]
    windows += [
        (session.started_at, session.ended_at)
        for session in sessions
        if window.contains(session.started_at)
    ]
    chosen: set[int] = set()
    for bounds in windows:
        chosen |= window_positions(segments, window=bounds, resolver=resolver)
    chosen |= {
        index + 1
        for index in chosen
        if index + 1 < len(segments) and ends_inside(segments[index], window)
    }
    return [segments[index] for index in sorted(chosen)]
