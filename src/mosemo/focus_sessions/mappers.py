from uuid import UUID

from mosemo.focus_sessions.models import FocusSession
from mosemo.focus_sessions.schemas import FocusSessionCreateRequest


class FocusSessionMapper:
    def to_entity(
        self, request: FocusSessionCreateRequest, *, account_id: UUID
    ) -> FocusSession:
        return FocusSession(account_id=account_id, **request.model_dump())
