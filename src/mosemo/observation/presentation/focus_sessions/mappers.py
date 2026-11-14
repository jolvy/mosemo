from uuid import UUID

from mosemo.observation.domain.focus_sessions import (
    FocusSession,
    FocusSessionCompletion,
    FocusSessionPause,
)
from mosemo.observation.presentation.focus_sessions.schemas import (
    FocusSessionCompleteRequest,
    FocusSessionCreateRequest,
)


class FocusSessionMapper:
    def to_entity(
        self, request: FocusSessionCreateRequest, *, account_id: UUID
    ) -> FocusSession:
        return FocusSession(
            id=request.id,
            account_id=account_id,
            device_id=request.device_id,
            started_at=request.started_at,
            target_seconds=request.target_seconds,
        )

    def to_completion(
        self, request: FocusSessionCompleteRequest
    ) -> FocusSessionCompletion:
        return FocusSessionCompletion(
            ended_at=request.ended_at,
            work_seconds=request.work_seconds,
            label_id=request.label_id,
            description=request.description,
            pauses=None
            if request.pauses is None
            else tuple(
                FocusSessionPause(started_at=pause.started_at, ended_at=pause.ended_at)
                for pause in request.pauses
            ),
        )
