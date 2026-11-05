from datetime import UTC, datetime
from uuid import uuid4

from mosemo.observation.presentation.focus_sessions.mappers import FocusSessionMapper
from mosemo.observation.presentation.focus_sessions.schemas import (
    FocusSessionCreateRequest,
)


def test_to_entity_copies_request_values_and_account_id() -> None:
    account_id = uuid4()
    request = FocusSessionCreateRequest.model_validate(
        {
            "id": uuid4(),
            "deviceId": uuid4(),
            "startedAt": "2026-09-14T09:00:00Z",
            "targetSeconds": 1500,
        }
    )

    entity = FocusSessionMapper().to_entity(request, account_id=account_id)

    assert entity.id == request.id
    assert entity.account_id == account_id
    assert entity.device_id == request.device_id
    assert entity.started_at == datetime(2026, 9, 14, 9, tzinfo=UTC)
    assert entity.target_seconds == request.target_seconds
    assert entity.ended_at is None
    assert entity.work_seconds is None
    assert entity.label_id is None
