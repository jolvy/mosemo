from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from mosemo.activities.constants import MAX_OBSERVATION_GAP
from mosemo.activities.enums import CollectionState, RecordType
from mosemo.activities.models import ActivityRecord


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


def _can_stop(
    segment: ProjectedSegment,
    *,
    seen_new_event: bool,
    stop_when_closed: Callable[[ProjectedSegment], bool] | None,
) -> bool:
    return seen_new_event and stop_when_closed is not None and stop_when_closed(segment)


def project_events(
    records: list[ActivityRecord],
    *,
    now: datetime | None = None,
    stop_after_event_id: UUID | None = None,
    stop_when_closed: Callable[[ProjectedSegment], bool] | None = None,
) -> list[ProjectedSegment]:
    segments: list[ProjectedSegment] = []
    seen_new_event = False
    for record in records:
        if record.event_id == stop_after_event_id:
            seen_new_event = True
        current = segments[-1] if segments else None
        if record.record_type is RecordType.ACTIVITY_OBSERVATION:
            context = cast(dict[str, object], record.payload["context"])
            if isinstance(current, ProjectedActivity):
                observed_gap = record.observed_at - current.last_observed_at
                if observed_gap <= MAX_OBSERVATION_GAP and context == current.context:
                    current.last_observed_at = record.observed_at
                    current.last_event_id = record.event_id
                    continue
                current.ended_at = (
                    record.observed_at
                    if observed_gap <= MAX_OBSERVATION_GAP
                    else current.last_observed_at
                )
                if _can_stop(
                    current,
                    seen_new_event=seen_new_event,
                    stop_when_closed=stop_when_closed,
                ):
                    return segments
            elif isinstance(current, ProjectedGap):
                current.ended_at = record.observed_at
                if _can_stop(
                    current,
                    seen_new_event=seen_new_event,
                    stop_when_closed=stop_when_closed,
                ):
                    return segments

            segments.append(
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
            if state is not CollectionState.SUSPENDED:
                continue
            if isinstance(current, ProjectedGap):
                continue
            if isinstance(current, ProjectedActivity):
                current.ended_at = (
                    record.observed_at
                    if record.observed_at - current.last_observed_at
                    <= MAX_OBSERVATION_GAP
                    else current.last_observed_at
                )
                if _can_stop(
                    current,
                    seen_new_event=seen_new_event,
                    stop_when_closed=stop_when_closed,
                ):
                    return segments
            segments.append(
                ProjectedGap(
                    started_at=record.observed_at,
                    first_event_id=record.event_id,
                    reason=cast(str, record.payload["reason"]),
                )
            )

    if now is not None and segments:
        current = segments[-1]
        if (
            isinstance(current, ProjectedActivity)
            and now - current.last_observed_at > MAX_OBSERVATION_GAP
        ):
            current.ended_at = current.last_observed_at
    return segments
