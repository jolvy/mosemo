import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid7

from mosemo.observation.domain.activities import (
    MAX_OBSERVATION_GAP,
    ActivityRecord,
    CollectionState,
    RecordType,
)
from mosemo.shared.domain.aggregates import AggregateRoot


class SegmentType(StrEnum):
    ACTIVITY = "activity"
    CAPTURE_GAP = "capture_gap"


class ActivityTimelineKind(StrEnum):
    CAPTURE_GAP = "capture_gap"
    OPAQUE_ACTIVITY = "opaque_activity"
    IN_PROGRESS_ACTIVITY = "in_progress_activity"
    CLOSED_DETAILED_ACTIVITY = "closed_detailed_activity"


class ActivityTimelineBusyError(Exception):
    pass


class ProjectionError(Exception):
    pass


@dataclass(eq=False, repr=False, kw_only=True)
class ActivityTimelineSegment(AggregateRoot):
    id: UUID = field(default_factory=uuid7)
    account_id: UUID
    segment_type: SegmentType
    started_at: datetime
    ended_at: datetime | None = None
    first_event_id: UUID
    last_event_id: UUID | None = None
    last_observed_at: datetime | None = None
    context: dict[str, object] | None = None
    reason: str | None = None

    def effective_ended_at(self, now: datetime) -> datetime | None:
        if self.segment_type is SegmentType.CAPTURE_GAP or self.ended_at is not None:
            return self.ended_at

        assert self.last_observed_at is not None
        if now - self.last_observed_at > MAX_OBSERVATION_GAP:
            return self.last_observed_at
        return None

    def overlaps_window(
        self,
        *,
        start: datetime,
        end: datetime,
        now: datetime,
    ) -> bool:
        ended_at = self.effective_ended_at(now)
        if ended_at is None:
            if self.segment_type is SegmentType.ACTIVITY:
                assert self.last_observed_at is not None
                return self.started_at < end and self.last_observed_at >= start
            return self.started_at < end
        if ended_at == self.started_at:
            return start <= self.started_at < end
        return self.started_at < end and ended_at > start

    def timeline_kind_at(self, now: datetime) -> ActivityTimelineKind:
        if self.segment_type is SegmentType.CAPTURE_GAP:
            return ActivityTimelineKind.CAPTURE_GAP

        assert self.context is not None
        context_kind = self.context.get("kind")
        if context_kind == "opaque":
            return ActivityTimelineKind.OPAQUE_ACTIVITY
        if context_kind != "detailed":
            raise ValueError(f"unsupported activity context kind: {context_kind!r}")
        if self.effective_ended_at(now) is None:
            return ActivityTimelineKind.IN_PROGRESS_ACTIVITY
        return ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY


def segment_version(
    segment: ActivityTimelineSegment,
    *,
    ended_at: datetime,
) -> str:
    assert segment.last_event_id is not None
    assert segment.last_observed_at is not None
    assert segment.context is not None
    canonical = json.dumps(
        {
            "context": segment.context,
            "endedAt": ended_at.astimezone(UTC).isoformat(),
            "firstEventId": str(segment.first_event_id),
            "lastEventId": str(segment.last_event_id),
            "lastObservedAt": segment.last_observed_at.astimezone(UTC).isoformat(),
            "startedAt": segment.started_at.astimezone(UTC).isoformat(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ProjectedActivity:
    started_at: datetime
    first_event_id: UUID
    last_event_id: UUID
    last_observed_at: datetime
    context: dict[str, object]
    ended_at: datetime | None = None

    def same_as(self, stored: ActivityTimelineSegment) -> bool:
        return (
            stored.segment_type is SegmentType.ACTIVITY
            and stored.started_at == self.started_at
            and stored.ended_at == self.ended_at
            and stored.first_event_id == self.first_event_id
            and stored.last_event_id == self.last_event_id
            and stored.last_observed_at == self.last_observed_at
            and stored.context == self.context
        )

    def reaches(self, observed_at: datetime) -> bool:
        return _reaches(self, observed_at)

    def converged_index(
        self, existing: list[ActivityTimelineSegment], *, observed_at: datetime
    ) -> int | None:
        return _converged_index(self, existing, observed_at=observed_at)

    def continues(self, stored: ActivityTimelineSegment) -> bool:
        return (
            self.can_inherit(stored)
            and stored.context == self.context
            and _starts_within(self, stored)
        )

    def can_inherit(self, stored: ActivityTimelineSegment) -> bool:
        return stored.segment_type is SegmentType.ACTIVITY

    def to_segment(
        self, *, account_id: UUID, segment_id: UUID | None
    ) -> ActivityTimelineSegment:
        segment = ActivityTimelineSegment(
            account_id=account_id,
            segment_type=SegmentType.ACTIVITY,
            started_at=self.started_at,
            ended_at=self.ended_at,
            first_event_id=self.first_event_id,
            last_event_id=self.last_event_id,
            last_observed_at=self.last_observed_at,
            context=self.context,
            reason=None,
        )
        if segment_id is not None:
            segment.id = segment_id
        return segment

    def close_at(self, observed_at: datetime) -> None:
        gap = observed_at - self.last_observed_at
        if gap.total_seconds() < 0:
            raise ProjectionError("observation_time_regression")
        self.ended_at = (
            observed_at if gap <= MAX_OBSERVATION_GAP else self.last_observed_at
        )


@dataclass(slots=True)
class ProjectedGap:
    started_at: datetime
    first_event_id: UUID
    reason: str
    ended_at: datetime | None = None

    def same_as(self, stored: ActivityTimelineSegment) -> bool:
        return (
            stored.segment_type is SegmentType.CAPTURE_GAP
            and stored.started_at == self.started_at
            and stored.ended_at == self.ended_at
            and stored.first_event_id == self.first_event_id
            and stored.reason == self.reason
        )

    def reaches(self, observed_at: datetime) -> bool:
        return _reaches(self, observed_at)

    def converged_index(
        self, existing: list[ActivityTimelineSegment], *, observed_at: datetime
    ) -> int | None:
        return _converged_index(self, existing, observed_at=observed_at)

    def continues(self, stored: ActivityTimelineSegment) -> bool:
        return self.can_inherit(stored) and _starts_within(self, stored)

    def can_inherit(self, stored: ActivityTimelineSegment) -> bool:
        return stored.segment_type is SegmentType.CAPTURE_GAP

    def to_segment(
        self, *, account_id: UUID, segment_id: UUID | None
    ) -> ActivityTimelineSegment:
        segment = ActivityTimelineSegment(
            account_id=account_id,
            segment_type=SegmentType.CAPTURE_GAP,
            started_at=self.started_at,
            ended_at=self.ended_at,
            first_event_id=self.first_event_id,
            last_event_id=None,
            last_observed_at=None,
            context=None,
            reason=self.reason,
        )
        if segment_id is not None:
            segment.id = segment_id
        return segment


ProjectedSegment = ProjectedActivity | ProjectedGap


def _reaches(segment: ProjectedSegment, observed_at: datetime) -> bool:
    if segment.started_at > observed_at:
        return True
    if segment.ended_at is None:
        return segment.started_at <= observed_at
    return segment.ended_at > observed_at


def _converged_index(
    segment: ProjectedSegment,
    existing: list[ActivityTimelineSegment],
    *,
    observed_at: datetime,
) -> int | None:
    if not segment.reaches(observed_at):
        return None
    for index, stored in enumerate(existing):
        if segment.same_as(stored):
            return index
    return None


def _starts_within(segment: ProjectedSegment, stored: ActivityTimelineSegment) -> bool:
    return stored.started_at >= segment.started_at and (
        segment.ended_at is None or stored.started_at <= segment.ended_at
    )


def observation_key(record: ActivityRecord) -> tuple[datetime, UUID, int, UUID]:
    # Per-device sequence chains merged by observed time. UUID resolves device ties,
    # never comparing one device's sequence against another's sequence.
    return record.observed_at, record.device_id, record.sequence, record.event_id


class TimelineProjection:
    """Pure incremental fold. Owns calculation state, never performs I/O."""

    def __init__(self, initial: ProjectedSegment | None = None) -> None:
        self.segments: list[ProjectedSegment] = [initial] if initial else []
        self.previous_time: datetime | None = (
            initial.last_observed_at
            if isinstance(initial, ProjectedActivity)
            else initial.started_at
            if initial is not None
            else None
        )

    def feed(self, record: ActivityRecord) -> ProjectedSegment | None:
        if self.previous_time is not None and record.observed_at < self.previous_time:
            raise ProjectionError("observation_time_regression")
        self.previous_time = record.observed_at
        current = self.segments[-1] if self.segments else None
        if record.record_type is RecordType.ACTIVITY_OBSERVATION:
            return self._feed_observation(record, current=current)
        if record.record_type is RecordType.COLLECTION_STATE_CHANGED:
            return self._feed_collection_state(record, current=current)
        return None

    def _feed_observation(
        self, record: ActivityRecord, *, current: ProjectedSegment | None
    ) -> ProjectedSegment | None:
        context = cast(dict[str, object], record.payload["context"])
        closed: ProjectedSegment | None = None
        if isinstance(current, ProjectedActivity):
            gap = record.observed_at - current.last_observed_at
            if (
                gap.total_seconds() >= 0
                and gap <= MAX_OBSERVATION_GAP
                and context == current.context
            ):
                current.last_observed_at = record.observed_at
                current.last_event_id = record.event_id
                current.ended_at = None
                return None
            current.close_at(record.observed_at)
            closed = current
        elif isinstance(current, ProjectedGap):
            current.ended_at = record.observed_at
            closed = current
        self.segments.append(
            ProjectedActivity(
                started_at=record.observed_at,
                first_event_id=record.event_id,
                last_event_id=record.event_id,
                last_observed_at=record.observed_at,
                context=context,
            )
        )
        return closed

    def _feed_collection_state(
        self, record: ActivityRecord, *, current: ProjectedSegment | None
    ) -> ProjectedSegment | None:
        state = CollectionState(cast(str, record.payload["state"]))
        if state is not CollectionState.SUSPENDED or isinstance(current, ProjectedGap):
            return None
        closed: ProjectedSegment | None = None
        if isinstance(current, ProjectedActivity):
            current.close_at(record.observed_at)
            closed = current
        self.segments.append(
            ProjectedGap(
                started_at=record.observed_at,
                first_event_id=record.event_id,
                reason=cast(str, record.payload["reason"]),
            )
        )
        return closed

    def finish(self, now: datetime | None) -> list[ProjectedSegment]:
        if now is not None and self.segments:
            current = self.segments[-1]
            if (
                isinstance(current, ProjectedActivity)
                and now - current.last_observed_at > MAX_OBSERVATION_GAP
            ):
                current.ended_at = current.last_observed_at
        return self.segments


def project_events(
    records: list[ActivityRecord],
    *,
    now: datetime | None = None,
    stop_after_event_id: UUID | None = None,
    stop_when_closed: Callable[[ProjectedSegment], bool] | None = None,
) -> list[ProjectedSegment]:
    projection = TimelineProjection()
    seen_new_event = False
    for record in records:
        if record.event_id == stop_after_event_id:
            seen_new_event = True
        closed = projection.feed(record)
        if (
            closed is not None
            and seen_new_event
            and stop_when_closed is not None
            and stop_when_closed(closed)
        ):
            # The boundary record belongs to the untouched suffix.
            return projection.segments[:-1]
    return projection.finish(now)
