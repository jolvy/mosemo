"""Translate persisted activity segments into label timeline domain values."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from mosemo.activities.enums import ActivityTimelineKind
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.labels.repository import ConfirmationWithLabelName
from mosemo.labels.timeline import (
    CaptureGap,
    ClosedActivity,
    InProgressActivity,
    LabelTimeline,
    OpaqueActivity,
    TimelineConfirmation,
    TimelineEntry,
)
from mosemo.labels.versions import segment_version


def build_label_timeline(
    *,
    segments: Sequence[ActivityTimelineSegment],
    confirmations: Mapping[UUID, ConfirmationWithLabelName],
    start: datetime,
    end: datetime,
    now: datetime,
) -> LabelTimeline:
    timeline = LabelTimeline()
    for segment in segments:
        if segment.overlaps_window(start=start, end=end, now=now):
            timeline.add(_entry(segment, confirmations=confirmations, now=now))
    return timeline


def _entry(
    segment: ActivityTimelineSegment,
    *,
    confirmations: Mapping[UUID, ConfirmationWithLabelName],
    now: datetime,
) -> TimelineEntry:
    kind = segment.timeline_kind_at(now)
    ended_at = segment.effective_ended_at(now)
    if kind is ActivityTimelineKind.CAPTURE_GAP:
        assert segment.reason is not None
        return CaptureGap(
            segment.segment_id, segment.started_at, ended_at, segment.reason
        )

    assert segment.context is not None
    assert segment.last_observed_at is not None
    if kind is ActivityTimelineKind.OPAQUE_ACTIVITY:
        return OpaqueActivity(
            segment.segment_id,
            segment.started_at,
            ended_at,
            segment.last_observed_at,
            segment.context,
        )
    if kind is ActivityTimelineKind.IN_PROGRESS_ACTIVITY:
        return InProgressActivity(
            segment.segment_id,
            segment.started_at,
            segment.last_observed_at,
            segment.context,
        )

    assert ended_at is not None
    version = segment_version(segment, ended_at=ended_at)
    return ClosedActivity(
        segment_id=segment.segment_id,
        segment_version=version,
        started_at=segment.started_at,
        ended_at=ended_at,
        last_observed_at=segment.last_observed_at,
        context=segment.context,
        confirmation=_current_confirmation(
            confirmations.get(segment.first_event_id), version=version
        ),
    )


def _current_confirmation(
    row: ConfirmationWithLabelName | None, *, version: str
) -> TimelineConfirmation | None:
    if row is None or row.confirmation.segment_version != version:
        return None
    return TimelineConfirmation(
        confirmation_id=row.confirmation.confirmation_id,
        label_id=row.confirmation.label_id,
        display_name=row.display_name,
        updated_at=row.confirmation.updated_at,
    )
