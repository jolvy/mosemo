from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, create_autospec, sentinel
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.entrypoints.api.activity_batches import get_batch_publisher
from mosemo.entrypoints.api.exception_handlers import register_exception_handlers
from mosemo.entrypoints.api.middleware import BatchBodyLimitMiddleware
from mosemo.identity.bootstrap import IdentityConfig
from mosemo.observation.application.activities import GetObservationTimeline
from mosemo.observation.application.batches import (
    AcceptActivityBatch,
    ProcessLegacyActivityBatch,
)
from mosemo.observation.application.timeline import ObservedSegment
from mosemo.observation.domain.timeline import ActivityTimelineKind
from mosemo.observation.presentation.activities.router import create_router
from mosemo.platform.clock import utc_now
from mosemo.platform.messaging.publisher import DeliveryUnavailableError
from mosemo.platform.providers import get_session
from mosemo.shared.application.clock import Clock
from mosemo.shared.presentation.current_account import get_current_account
from tests.support.clock import FakeClock
from tests.support.identity import current_account_provider, token_service
from tests.support.observation import activity_request
from tests.support.use_cases import record_transactions

# `activity_request()` records are observed at 2026-09-14T00:00:00Z.
ACCEPTED_AT = datetime(2026, 9, 14, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_database_transactions(monkeypatch: pytest.MonkeyPatch) -> None:
    # The device check runs on the mocked directory; no database is involved.
    record_transactions(
        monkeypatch, "mosemo.observation.application.batches", "read_snapshot"
    )


def make_app(
    *,
    identity_config: IdentityConfig,
    account_id: UUID,
    service: GetObservationTimeline,
    acceptance_clock: Clock | None = None,
):
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(BatchBodyLimitMiddleware)
    app.state.devices = AsyncMock()
    app.state.batch_publisher = AsyncMock()
    app.include_router(
        create_router(
            accept_activity_batch=AcceptActivityBatch(
                devices=app.state.devices,
                acceptance_clock=acceptance_clock or FakeClock(ACCEPTED_AT),
            ),
            process_legacy_activity_batch=create_autospec(
                ProcessLegacyActivityBatch, instance=True
            ),
            get_observation_timeline=service,
            get_batch_publisher=get_batch_publisher,
        ),
        prefix="/api/v1",
    )
    app.dependency_overrides[get_session] = lambda: sentinel.db_session
    app.dependency_overrides[get_current_account] = current_account_provider(
        identity_config
    )
    token = token_service(identity_config).issue_access_token(account_id, now=utc_now())
    return app, token


def test_batch_response_preserves_camel_case_and_server_account(identity_config):
    account_id = uuid4()
    app, token = make_app(
        identity_config=identity_config,
        account_id=account_id,
        service=create_autospec(GetObservationTimeline, instance=True),
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


def test_batch_admission_hides_missing_and_foreign_devices(identity_config):
    app, token = make_app(
        identity_config=identity_config,
        account_id=uuid4(),
        service=create_autospec(GetObservationTimeline, instance=True),
    )
    app.state.devices.find_owned.return_value = None
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
def test_batch_rejects_invalid_record_before_admission(identity_config, invalid):
    app, token = make_app(
        identity_config=identity_config,
        account_id=uuid4(),
        service=create_autospec(GetObservationTimeline, instance=True),
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


def outside_window_request() -> dict:
    """Records at 0 and 2 are outside the window of `ACCEPTED_AT`; 1 is inside."""
    payload = activity_request()
    inside = payload["records"][0]
    payload["records"] = [
        {**inside, "eventId": str(uuid4()), "sequence": sequence, "observedAt": at}
        for sequence, at in enumerate(
            [
                "2026-09-13T00:59:59.999Z",
                "2026-09-14T00:00:00Z",
                "2026-09-14T01:00:00.001Z",
            ]
        )
    ]
    return payload


def test_batch_rejects_records_outside_acceptance_window_without_publishing(
    identity_config,
):
    app, token = make_app(
        identity_config=identity_config,
        account_id=uuid4(),
        service=create_autospec(GetObservationTimeline, instance=True),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities/batches",
            headers={"Authorization": f"Bearer {token}"},
            json=outside_window_request(),
        )
    message = "Activity record is outside the acceptance window"
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "status": "ACTIVITY_RECORD_OUTSIDE_ACCEPTANCE_WINDOW",
            "code": 422,
            "message": message,
            "details": [
                {
                    "loc": ["body", "records", index, "observedAt"],
                    "msg": message,
                    "type": "activity_record_outside_acceptance_window",
                }
                for index in (0, 2)
            ],
        }
    }
    app.state.batch_publisher.publish.assert_not_called()


@pytest.mark.parametrize(
    "accepted_at",
    [
        datetime(2026, 9, 14, tzinfo=UTC),
        datetime(2026, 9, 15, tzinfo=UTC),
    ],
    ids=["same_instant", "24_hours_later"],
)
def test_batch_accepts_records_at_acceptance_window_edges(
    identity_config, accepted_at: datetime
):
    app, token = make_app(
        identity_config=identity_config,
        account_id=uuid4(),
        service=create_autospec(GetObservationTimeline, instance=True),
        acceptance_clock=FakeClock(accepted_at),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities/batches",
            headers={"Authorization": f"Bearer {token}"},
            json=activity_request(),
        )
    assert response.status_code == 202
    app.state.batch_publisher.publish.assert_awaited_once()


def test_batch_hides_unowned_device_before_acceptance_window(identity_config):
    app, token = make_app(
        identity_config=identity_config,
        account_id=uuid4(),
        service=create_autospec(GetObservationTimeline, instance=True),
        acceptance_clock=FakeClock(ACCEPTED_AT + timedelta(days=30)),
    )
    app.state.devices.find_owned.return_value = None
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities/batches",
            headers={"Authorization": f"Bearer {token}"},
            json=outside_window_request(),
        )
    assert response.status_code == 404
    assert response.json()["error"]["status"] == "ACTIVITY_DEVICE_NOT_FOUND"
    app.state.batch_publisher.publish.assert_not_called()


def test_batch_rejects_acceptance_window_before_delivery(identity_config):
    app, token = make_app(
        identity_config=identity_config,
        account_id=uuid4(),
        service=create_autospec(GetObservationTimeline, instance=True),
    )
    app.state.batch_publisher = None
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities/batches",
            headers={"Authorization": f"Bearer {token}"},
            json=outside_window_request(),
        )
    assert response.status_code == 422
    assert response.json()["error"]["status"] == (
        "ACTIVITY_RECORD_OUTSIDE_ACCEPTANCE_WINDOW"
    )


def test_batch_admission_returns_common_retryable_delivery_error(identity_config):
    app, token = make_app(
        identity_config=identity_config,
        account_id=uuid4(),
        service=create_autospec(GetObservationTimeline, instance=True),
    )
    app.state.batch_publisher.publish.side_effect = DeliveryUnavailableError()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/activities/batches",
            headers={"Authorization": f"Bearer {token}"},
            json=activity_request(),
        )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"]["status"] == "ACTIVITY_DELIVERY_UNAVAILABLE"


def test_timeline_returns_direct_discriminated_union_array(
    identity_config: IdentityConfig,
) -> None:
    account_id = uuid4()
    service = create_autospec(GetObservationTimeline, instance=True)
    activity_id = uuid4()
    gap_id = uuid4()
    service.execute.return_value = [
        ObservedSegment(
            id=activity_id,
            account_id=account_id,
            first_event_id=uuid4(),
            started_at=datetime(2026, 9, 14, 0, 0, 0, 123456, tzinfo=UTC),
            ended_at=datetime(2026, 9, 14, 0, 0, 30, tzinfo=UTC),
            last_observed_at=datetime(2026, 9, 14, 0, 0, 20, tzinfo=UTC),
            context={"kind": "opaque"},
            reason=None,
            kind=ActivityTimelineKind.OPAQUE_ACTIVITY,
            version=None,
        ),
        ObservedSegment(
            id=gap_id,
            account_id=account_id,
            first_event_id=uuid4(),
            started_at=datetime(2026, 9, 14, 0, 0, 30, tzinfo=UTC),
            ended_at=None,
            last_observed_at=None,
            context=None,
            reason="screen_locked",
            kind=ActivityTimelineKind.CAPTURE_GAP,
            version=None,
        ),
    ]
    app, token = make_app(
        identity_config=identity_config, account_id=account_id, service=service
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(activity_id),
            "segmentType": "activity",
            "startedAt": "2026-09-14T00:00:00.123456Z",
            "endedAt": "2026-09-14T00:00:30Z",
            "lastObservedAt": "2026-09-14T00:00:20Z",
            "context": {"kind": "opaque"},
        },
        {
            "id": str(gap_id),
            "segmentType": "capture_gap",
            "startedAt": "2026-09-14T00:00:30Z",
            "endedAt": None,
            "reason": "screen_locked",
        },
    ]
    service.execute.assert_awaited_once_with(
        sentinel.db_session,
        account_id=account_id,
        day=date(2026, 9, 14),
    )


def test_timeline_requires_bearer_and_date(identity_config: IdentityConfig) -> None:
    account_id = uuid4()
    service = create_autospec(GetObservationTimeline, instance=True)
    app, token = make_app(
        identity_config=identity_config, account_id=account_id, service=service
    )

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
    service.execute.assert_not_awaited()


def test_batch_body_limit_counts_complete_utf8_envelope_including_whitespace(
    identity_config,
):
    import json

    app, token = make_app(
        identity_config=identity_config,
        account_id=uuid4(),
        service=create_autospec(GetObservationTimeline, instance=True),
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
