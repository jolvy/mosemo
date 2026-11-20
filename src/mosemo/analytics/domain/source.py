"""Analysis copies of the source data: segments, confirmations, completed sessions."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from itertools import pairwise
from zoneinfo import ZoneInfo

from mosemo.shared.domain.timezones import Timezone

MICROSECOND = timedelta(microseconds=1)

Interval = tuple[datetime, datetime]


def micros(delta: timedelta) -> int:
    return delta // MICROSECOND


class InvalidSourceError(ValueError):
    """A source snapshot that breaks the D1 input contract and cannot be computed."""


class SegmentKind(StrEnum):
    ACTIVITY = "activity"
    CAPTURE_GAP = "capture_gap"


@dataclass(frozen=True, kw_only=True)
class SourceSegment:
    """An observation timeline segment as analysis reads it.

    `ended_at` is None for an open segment. `observed_end` is
    `COALESCE(ended_at, last_observed_at)` for activity (FR-6) and `ended_at` for a
    capture gap (None while the gap is open).
    """

    id: str
    first_event_id: str
    kind: SegmentKind
    started_at: datetime
    ended_at: datetime | None
    observed_end: datetime | None
    version: str | None = None
    context: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.ended_at is not None and self.observed_end != self.ended_at:
            raise ValueError(f"segment {self.id}: observed_end must equal ended_at")
        if self.is_activity and self.observed_end is None:
            raise ValueError(f"activity {self.id} needs its last observation")
        if self.observed_end is not None and self.observed_end < self.started_at:
            raise ValueError(f"segment {self.id} ends before it starts")

    @property
    def is_activity(self) -> bool:
        return self.kind is SegmentKind.ACTIVITY

    @property
    def is_opaque(self) -> bool:
        return self.is_activity and (self.context or {}).get("kind") == "opaque"

    @property
    def is_detailed_activity(self) -> bool:
        return self.is_activity and not self.is_opaque

    @property
    def length_us(self) -> int:
        """Observed length; an open capture gap has no length."""
        return micros((self.observed_end or self.started_at) - self.started_at)

    def adjoins(self, after: SourceSegment) -> bool:
        """The next segment starts exactly where this closed segment ended."""
        return self.ended_at is not None and self.ended_at == after.started_at

    def overlap_us(self, window: Interval) -> int:
        """Observed time inside the window; an open capture gap has none."""
        if self.observed_end is None:
            return 0
        start, end = window
        return max(micros(min(self.observed_end, end) - max(self.started_at, start)), 0)


@dataclass(frozen=True, kw_only=True)
class ConfirmedLabel:
    """A label confirmation as recorded for one segment version (None: unclassified)."""

    segment_version: str
    label_id: str | None


@dataclass(frozen=True, kw_only=True)
class CompletedFocusSession:
    """A completed focus session. `pauses` is None when pause information is missing."""

    id: str
    started_at: datetime
    ended_at: datetime
    label_id: str
    pauses: tuple[Interval, ...] | None

    def __post_init__(self) -> None:
        if self.ended_at < self.started_at:
            raise ValueError(f"session {self.id} ends before it starts")

    @property
    def has_pause_info(self) -> bool:
        return self.pauses is not None

    def work_intervals(self) -> tuple[Interval, ...]:
        """The session analysis time: start to end without the pauses."""
        cursor = self.started_at
        intervals: list[Interval] = []
        for pause_start, pause_end in sorted(self.pauses or ()):
            if pause_start > cursor:
                intervals.append((cursor, pause_start))
            cursor = max(cursor, pause_end)
        if self.ended_at > cursor:
            intervals.append((cursor, self.ended_at))
        return tuple(intervals)


@dataclass(frozen=True, kw_only=True)
class DayWindow:
    """An account-timezone date as `[start, end)` in UTC (`timeline_date_window`)."""

    day: date
    start: datetime
    end: datetime

    @classmethod
    def of(cls, timezone: Timezone, day: date) -> DayWindow:
        zone = ZoneInfo(timezone.value)
        try:
            start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
        except OverflowError:
            start = datetime.min.replace(tzinfo=UTC)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
        return cls(day=day, start=start, end=end.astimezone(UTC))

    @property
    def bounds(self) -> Interval:
        return self.start, self.end

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


@dataclass(frozen=True, kw_only=True)
class SourceSnapshot:
    """One account's source data read in one snapshot.

    Segments are in start order and do not overlap (zero-length segments at the same
    instant adjoin). Only the last segment may be open (D1 input contract).
    `confirmations` maps a segment's `first_event_id` to its confirmation.
    """

    timezone: Timezone
    segments: tuple[SourceSegment, ...]
    confirmations: Mapping[str, ConfirmedLabel] = field(default_factory=dict)
    sessions: tuple[CompletedFocusSession, ...] = ()

    def __post_init__(self) -> None:
        for before, after in pairwise(self.segments):
            if before.observed_end is None or before.ended_at is None:
                raise InvalidSourceError(
                    f"open segment {before.id} must be the last one"
                )
            if before.observed_end > after.started_at:
                raise InvalidSourceError(f"segments {before.id} and {after.id} overlap")

    def confirmation(self, segment: SourceSegment) -> ConfirmedLabel | None:
        """The valid confirmation: recorded for the segment's current version."""
        if not segment.is_activity:
            return None
        stored = self.confirmations.get(segment.first_event_id)
        if stored is None or stored.segment_version != segment.version:
            return None
        return stored

    def label(self, segment: SourceSegment) -> str | None:
        """The C7 label: none for opaque, pending or unclassified activity (FR-23)."""
        if segment.is_opaque:
            return None
        confirmation = self.confirmation(segment)
        return confirmation.label_id if confirmation else None

    def with_segments(self, segments: tuple[SourceSegment, ...]) -> SourceSnapshot:
        return SourceSnapshot(
            timezone=self.timezone,
            segments=segments,
            confirmations=self.confirmations,
            sessions=self.sessions,
        )
