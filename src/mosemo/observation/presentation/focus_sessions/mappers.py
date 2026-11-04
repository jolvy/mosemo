from uuid import UUID

from mosemo.observation.application.focus_sessions import FocusSessionCompletion
from mosemo.observation.domain.focus_sessions import FocusSession
from mosemo.observation.presentation.focus_sessions.schemas import (
    FocusSessionCompleteRequest,
    FocusSessionCreateRequest,
)


class FocusSessionMapper:
    def to_entity(
        self, request: FocusSessionCreateRequest, *, account_id: UUID
    ) -> FocusSession:
        return FocusSession(
            id=request.session_id,
            account_id=account_id,
            device_id=request.device_id,
            started_at=request.started_at,
            target_seconds=request.target_seconds,
        )

    def to_completion(
        self, request: FocusSessionCompleteRequest
    ) -> FocusSessionCompletion:
        return FocusSessionCompletion(**request.model_dump())
