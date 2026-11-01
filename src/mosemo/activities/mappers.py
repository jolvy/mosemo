from mosemo.activities.models import ActivityRecord
from mosemo.activities.schemas import ActivityObservation
from mosemo.activities.schemas import ActivityRecord as ActivityRecordRequest


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
