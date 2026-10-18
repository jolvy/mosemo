from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, create_autospec
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.activities.enums import SegmentType
from mosemo.activities.schemas import ActivitySegmentResponse, CaptureGapResponse
from mosemo.activities.service import ActivityService
from mosemo.api import v1_api_router
from mosemo.auth.tokens import TokenService
from mosemo.config import Config, get_config
from mosemo.dependencies import get_activity_service, get_device_repository
from mosemo.exception_handlers import register_exception_handlers
from mosemo.infra.batch_delivery import DeliveryUnavailable
from mosemo.middleware import BatchBodyLimitMiddleware


def activity_request():
    return {
        "batchId": str(uuid4()),
        "deviceId": str(uuid4()),
        "records": [
            {
                "eventId": str(uuid4()),
                "sequence": 3,
                "recordType": "activity_observation",
                "observedAt": "2026-09-14T00:00:00Z",
                "timezoneId": "Asia/Seoul",
                "utcOffsetMinutes": 540,
                "context": {"kind": "opaque"},
            }
        ],
    }


def make_app(*, config: Config, account_id: UUID, service: ActivityService):
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(BatchBodyLimitMiddleware)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_activity_service] = lambda: service
    app.state.devices = AsyncMock()
    app.state.batch_publisher = AsyncMock()
    app.dependency_overrides[get_device_repository] = lambda: app.state.devices
    token = TokenService(config.auth).issue_access_token(account_id)
    return app, token


def test_batch_response_preserves_camel_case_and_server_account(config):
    account_id = uuid4()
    app, token = make_app(
        config=config,
        account_id=account_id,
        service=create_autospec(ActivityService, instance=True),
    )
    payload = activity_request()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities/batches",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
    assert response.status_code == 202
    assert response.json() == {"batchId": payload["batchId"], "status": "accepted"}
    owner, message = app.state.batch_publisher.publish.call_args.args
    assert owner == account_id
    assert message.device_id == UUID(payload["deviceId"])
    assert len(message.records) == 1


def test_batch_admission_hides_missing_and_foreign_devices(config):
    app, token = make_app(
        config=config,
        account_id=uuid4(),
        service=create_autospec(ActivityService, instance=True),
    )
    app.state.devices.find_owned_by_id.return_value = None
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities/batches",
            headers={"Authorization": f"Bearer {token}"},
            json=activity_request(),
        )
    assert response.status_code == 404
    assert response.json()["error"]["status"] == "ACTIVITY_DEVICE_NOT_FOUND"
    app.state.batch_publisher.publish.assert_not_called()


@pytest.mark.parametrize(
    "invalid",
    ["legacy_device", "timezone", "record_type", "state", "privacy", "nested_device"],
)
def test_batch_rejects_invalid_record_before_admission(config, invalid):
    app, token = make_app(
        config=config,
        account_id=uuid4(),
        service=create_autospec(ActivityService, instance=True),
    )
    payload = activity_request()
    record = payload["records"][0]
    if invalid == "legacy_device":
        payload["deviceRegistrationId"] = payload.pop("deviceId")
    elif invalid == "timezone":
        record["timezoneId"] = "Europe/Paris"
    elif invalid == "record_type":
        record["recordType"] = "unsupported"
    elif invalid == "state":
        record.pop("context")
        record.update(
            recordType="collection_state_changed", state="unsupported", reason="locked"
        )
    elif invalid == "privacy":
        record["context"] = {"kind": "opaque", "title": "forbidden"}
    else:
        record["deviceId"] = payload["deviceId"]
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities/batches",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
    assert response.status_code == 422
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"
    app.state.batch_publisher.publish.assert_not_called()


def test_batch_admission_returns_common_retryable_delivery_error(config):
    app, token = make_app(
        config=config,
        account_id=uuid4(),
        service=create_autospec(ActivityService, instance=True),
    )
    app.state.batch_publisher.publish.side_effect = DeliveryUnavailable()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities/batches",
            headers={"Authorization": f"Bearer {token}"},
            json=activity_request(),
        )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"]["status"] == "ACTIVITY_DELIVERY_UNAVAILABLE"


def test_timeline_returns_direct_discriminated_union_array(config: Config) -> None:
    account_id = uuid4()
    service = create_autospec(ActivityService, instance=True)
    activity_id = uuid4()
    gap_id = uuid4()
    service.get_timeline.return_value = [
        ActivitySegmentResponse(
            segment_id=activity_id,
            segment_type=SegmentType.ACTIVITY,
            started_at=datetime(2026, 9, 14, 0, 0, 0, 123456, tzinfo=UTC),
            ended_at=datetime(2026, 9, 14, 0, 0, 30, tzinfo=UTC),
            last_observed_at=datetime(2026, 9, 14, 0, 0, 20, tzinfo=UTC),
            context={"kind": "opaque"},
        ),
        CaptureGapResponse(
            segment_id=gap_id,
            segment_type=SegmentType.CAPTURE_GAP,
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


def test_batch_body_limit_counts_complete_utf8_envelope_including_whitespace(config):
    import json

    app, token = make_app(
        config=config,
        account_id=uuid4(),
        service=create_autospec(ActivityService, instance=True),
    )
    encoded = json.dumps(activity_request(), ensure_ascii=False).encode()
    bounded = encoded + b" " * (512 * 1024 - len(encoded))
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/activities/batches", headers=headers, content=bounded
            ).status_code
            == 202
        )
        assert (
            client.post(
                "/api/v1/activities/batches", headers=headers, content=bounded + b" "
            ).status_code
            == 413
        )
    app.state.batch_publisher.publish.assert_awaited_once()
    assert len(app.state.batch_publisher.publish.call_args.args[1].records) == 1
