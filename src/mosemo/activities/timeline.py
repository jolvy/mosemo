from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from mosemo.activities.constants import MAX_OBSERVATION_GAP
from mosemo.activities.enums import CollectionState, RecordType
from mosemo.activities.models import ActivityRecord


class ProjectionFailure(Exception):
    pass


@dataclass(slots=True)
class ProjectedActivity:
    started_at: datetime
    first_event_id: UUID
    last_event_id: UUID
    last_observed_at: datetime
    context: dict[str, object]
    ended_at: datetime | None = None


@dataclass(slots=True)
class ProjectedGap:
    started_at: datetime
    first_event_id: UUID
    reason: str
    ended_at: datetime | None = None


ProjectedSegment = ProjectedActivity | ProjectedGap


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
            raise ProjectionFailure("observation_time_regression")
        self.previous_time = record.observed_at
        current = self.segments[-1] if self.segments else None
        closed: ProjectedSegment | None = None
        if record.record_type is RecordType.ACTIVITY_OBSERVATION:
            context = cast(dict[str, object], record.payload["context"])
            if isinstance(current, ProjectedActivity):
                gap = record.observed_at - current.last_observed_at
                if gap.total_seconds() < 0:
                    raise ProjectionFailure("observation_time_regression")
                if gap <= MAX_OBSERVATION_GAP and context == current.context:
                    current.last_observed_at = record.observed_at
                    current.last_event_id = record.event_id
                    current.ended_at = None
                    return None
                current.ended_at = (
                    record.observed_at
                    if gap <= MAX_OBSERVATION_GAP
                    else current.last_observed_at
                )
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
        elif record.record_type is RecordType.COLLECTION_STATE_CHANGED:
            state = CollectionState(cast(str, record.payload["state"]))
            if state is not CollectionState.SUSPENDED or isinstance(
                current, ProjectedGap
            ):
                return None
            if isinstance(current, ProjectedActivity):
                gap = record.observed_at - current.last_observed_at
                if gap.total_seconds() < 0:
                    raise ProjectionFailure("observation_time_regression")
                current.ended_at = (
                    record.observed_at
                    if gap <= MAX_OBSERVATION_GAP
                    else current.last_observed_at
                )
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
