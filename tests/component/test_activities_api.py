from datetime import UTC, date, datetime
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.activities.schemas import (
    ActivityCreateResponse,
    ActivitySegmentResponse,
    CaptureGapResponse,
)
from mosemo.activities.service import (
    ActivityDeviceNotFoundError,
    ActivityEventIdConflictError,
    ActivitySequenceConflictError,
    ActivityService,
    ActivityTimelineBusyError,
)
from mosemo.api import v1_api_router
from mosemo.auth.tokens import TokenService
from mosemo.config import Config, get_config
from mosemo.dependencies import get_activity_service
from mosemo.exception_handlers import register_exception_handlers


def activity_request(*, event_id: str | None = None) -> dict[str, object]:
    return {
        "deviceId": "30000000-0000-0000-0000-000000000000",
        "eventId": event_id or "40000000-0000-0000-0000-000000000000",
        "sequence": 3,
        "recordType": "activity_observation",
        "observedAt": "2026-09-14T00:00:00Z",
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "context": {"kind": "opaque"},
    }


def make_app(
    *,
    config: Config,
    account_id: UUID,
    service: ActivityService,
) -> tuple[FastAPI, str]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_activity_service] = lambda: service
    token = TokenService(config.auth).issue_access_token(account_id)
    return app, token


def test_activities_create_returns_camel_case_created_response(
    config: Config,
) -> None:
    account_id = uuid4()
    service = create_autospec(ActivityService, instance=True)
    event_id = uuid4()
    service.create_activity.return_value = ActivityCreateResponse(
        event_id=event_id,
        status="accepted",
        received_at=datetime(2026, 9, 14, 1, 2, 3, 456789, tzinfo=UTC),
    )
    app, token = make_app(config=config, account_id=account_id, service=service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities",
            headers={"Authorization": f"Bearer {token}"},
            json=activity_request(event_id=str(event_id)),
        )

    assert response.status_code == 201
    assert response.json() == {
        "eventId": str(event_id),
        "status": "accepted",
        "receivedAt": "2026-09-14T01:02:03Z",
    }
    call = service.create_activity.await_args
    assert call.kwargs["account_id"] == account_id
    assert call.kwargs["record"].event_id == event_id


@pytest.mark.parametrize(
    ("service_error", "http_status", "public_status", "message"),
    [
        (
            ActivityDeviceNotFoundError(),
            404,
            "ACTIVITY_DEVICE_NOT_FOUND",
            "Activity device not found",
        ),
        (
            ActivityEventIdConflictError(),
            409,
            "ACTIVITY_EVENT_ID_CONFLICT",
            "Activity event ID conflicts with a stored record",
        ),
        (
            ActivitySequenceConflictError(),
            409,
            "ACTIVITY_SEQUENCE_CONFLICT",
            "Activity sequence conflicts with a stored record",
        ),
    ],
)
def test_activities_create_returns_public_storage_errors(
    config: Config,
    service_error: Exception,
    http_status: int,
    public_status: str,
    message: str,
) -> None:
    account_id = uuid4()
    service = create_autospec(ActivityService, instance=True)
    service.create_activity.side_effect = service_error
    app, token = make_app(config=config, account_id=account_id, service=service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities",
            headers={"Authorization": f"Bearer {token}"},
            json=activity_request(),
        )

    assert response.status_code == http_status
    assert response.json() == {
        "error": {
            "status": public_status,
            "code": http_status,
            "message": message,
            "details": [],
        }
    }


def test_activities_create_rejects_legacy_device_field(config: Config) -> None:
    account_id = uuid4()
    service = create_autospec(ActivityService, instance=True)
    app, token = make_app(config=config, account_id=account_id, service=service)
    request = activity_request()
    request["deviceRegistrationId"] = request.pop("deviceId")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities",
            headers={"Authorization": f"Bearer {token}"},
            json=request,
        )

    assert response.status_code == 422
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"
    service.create_activity.assert_not_awaited()


def test_activities_create_returns_retryable_busy_error(config: Config) -> None:
    account_id = uuid4()
    service = create_autospec(ActivityService, instance=True)
    service.create_activity.side_effect = ActivityTimelineBusyError()
    app, token = make_app(config=config, account_id=account_id, service=service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities",
            headers={"Authorization": f"Bearer {token}"},
            json=activity_request(),
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json() == {
        "error": {
            "status": "ACTIVITY_TIMELINE_BUSY",
            "code": 503,
            "message": "Activity timeline is busy",
            "details": [],
        }
    }


def test_timeline_returns_direct_discriminated_union_array(config: Config) -> None:
    account_id = uuid4()
    service = create_autospec(ActivityService, instance=True)
    activity_id = uuid4()
    gap_id = uuid4()
    service.get_timeline.return_value = [
        ActivitySegmentResponse(
            segment_id=activity_id,
            segment_type="activity",
            started_at=datetime(2026, 9, 14, 0, 0, 0, 123456, tzinfo=UTC),
            ended_at=datetime(2026, 9, 14, 0, 0, 30, tzinfo=UTC),
            last_observed_at=datetime(2026, 9, 14, 0, 0, 20, tzinfo=UTC),
            context={"kind": "opaque"},
        ),
        CaptureGapResponse(
            segment_id=gap_id,
            segment_type="capture_gap",
            started_at=datetime(2026, 9, 14, 0, 0, 30, tzinfo=UTC),
            ended_at=None,
            reason="screen_locked",
        ),
    ]
    app, token = make_app(config=config, account_id=account_id, service=service)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == [
        {
            "segmentId": str(activity_id),
            "segmentType": "activity",
            "startedAt": "2026-09-14T00:00:00.123456Z",
            "endedAt": "2026-09-14T00:00:30Z",
            "lastObservedAt": "2026-09-14T00:00:20Z",
            "context": {"kind": "opaque"},
        },
        {
            "segmentId": str(gap_id),
            "segmentType": "capture_gap",
            "startedAt": "2026-09-14T00:00:30Z",
            "endedAt": None,
            "reason": "screen_locked",
        },
    ]
    service.get_timeline.assert_awaited_once_with(
        account_id=account_id,
        date=date(2026, 9, 14),
    )


def test_timeline_requires_bearer_and_date(config: Config) -> None:
    account_id = uuid4()
    service = create_autospec(ActivityService, instance=True)
    app, token = make_app(config=config, account_id=account_id, service=service)

    with TestClient(app) as client:
        missing_auth = client.get("/api/v1/activities/timeline?date=2026-09-14")
        missing_date = client.get(
            "/api/v1/activities/timeline",
            headers={"Authorization": f"Bearer {token}"},
        )
        invalid_date = client.get(
            "/api/v1/activities/timeline?date=bad",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert missing_auth.status_code == 401
    assert missing_auth.json()["error"]["status"] == "AUTH_INVALID_ACCESS_TOKEN"
    for response in (missing_date, invalid_date):
        assert response.status_code == 422
        assert response.json()["error"]["status"] == "INVALID_ARGUMENT"
    service.get_timeline.assert_not_awaited()
