from datetime import UTC, datetime
from uuid import uuid4

from mosemo.observation.presentation.focus_sessions.mappers import FocusSessionMapper
from mosemo.observation.presentation.focus_sessions.schemas import (
    FocusSessionCompleteRequest,
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


def complete_request(**changes: object) -> FocusSessionCompleteRequest:
    return FocusSessionCompleteRequest.model_validate(
        {
            "endedAt": "2026-09-14T09:20:00Z",
            "workSeconds": 600,
            "labelId": str(uuid4()),
            **changes,
        }
    )


def test_to_completion_keeps_missing_pauses_as_no_pause_info() -> None:
    completion = FocusSessionMapper().to_completion(complete_request())

    assert completion.pauses is None
    assert completion.description == ""


def test_to_completion_copies_pauses_in_request_order() -> None:
    request = complete_request(
        pauses=[
            {"startedAt": "2026-09-14T09:10:00Z", "endedAt": "2026-09-14T09:12:00Z"},
            {"startedAt": "2026-09-14T09:01:00Z", "endedAt": "2026-09-14T09:01:00Z"},
        ]
    )

    completion = FocusSessionMapper().to_completion(request)

    assert completion.pauses is not None
    assert [pause.span() for pause in completion.pauses] == [
        (
            datetime(2026, 9, 14, 9, 10, tzinfo=UTC),
            datetime(2026, 9, 14, 9, 12, tzinfo=UTC),
        ),
        (
            datetime(2026, 9, 14, 9, 1, tzinfo=UTC),
            datetime(2026, 9, 14, 9, 1, tzinfo=UTC),
        ),
    ]
