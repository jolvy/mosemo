import json
from hashlib import sha256

from pydantic import TypeAdapter

from mosemo.observation.application.batches import ActivityBatch
from mosemo.observation.application.timeline import ObservedSegment
from mosemo.observation.domain.activities import ActivityRecord
from mosemo.observation.domain.timeline import ActivityTimelineKind, SegmentType
from mosemo.observation.presentation.activities.schemas import (
    ActivityBatchRequest,
    ActivityContext,
    ActivityObservation,
    ActivitySegmentResponse,
    CaptureGapResponse,
    TimelineSegmentResponse,
)
from mosemo.observation.presentation.activities.schemas import (
    ActivityRecord as ActivityRecordRequest,
)

context_adapter = TypeAdapter(ActivityContext)


class ActivityRecordMapper:
    def to_entity(self, request: ActivityRecordRequest) -> ActivityRecord:
        return ActivityRecord(
            event_id=request.event_id,
            device_id=request.device_id,
            focus_session_id=request.focus_session_id,
            sequence=request.sequence,
            record_type=request.record_type,
            observed_at=request.observed_at,
            timezone_id=request.timezone_id,
            utc_offset_minutes=request.utc_offset_minutes,
            payload=self._payload(request),
        )

    @staticmethod
    def _payload(request: ActivityRecordRequest) -> dict[str, object]:
        if isinstance(request, ActivityObservation):
            return {"context": request.context.model_dump(mode="json", by_alias=True)}
        return {"state": request.state.value, "reason": request.reason}


def batch_fingerprint(batch: ActivityBatchRequest) -> str:
    canonical = json.dumps(
        batch.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(canonical.encode()).hexdigest()


def activity_batch(batch: ActivityBatchRequest) -> ActivityBatch:
    mapper = ActivityRecordMapper()
    return ActivityBatch(
        batch_id=batch.batch_id,
        device_id=batch.device_id,
        fingerprint=batch_fingerprint(batch),
        records=tuple(mapper.to_entity(record) for record in batch.activity_records()),
    )


def timeline_segment_response(segment: ObservedSegment) -> TimelineSegmentResponse:
    if segment.kind is not ActivityTimelineKind.CAPTURE_GAP:
        assert segment.last_observed_at is not None
        assert segment.context is not None
        return ActivitySegmentResponse(
            segment_id=segment.id,
            segment_type=SegmentType.ACTIVITY,
            started_at=segment.started_at,
            ended_at=segment.ended_at,
            last_observed_at=segment.last_observed_at,
            context=context_adapter.validate_python(segment.context),
        )

    assert segment.reason is not None
    return CaptureGapResponse(
        segment_id=segment.id,
        segment_type=SegmentType.CAPTURE_GAP,
        started_at=segment.started_at,
        ended_at=segment.ended_at,
        reason=segment.reason,
    )
