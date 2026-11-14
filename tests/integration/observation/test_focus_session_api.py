import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mosemo.entrypoints.api.exception_handlers import register_exception_handlers
from mosemo.entrypoints.api.routes import v1_api_router
from mosemo.identity.bootstrap import IdentityConfig
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.devices import Device
from mosemo.labels.domain.labels import Label
from mosemo.labels.infrastructure.tables import labels as label_table
from mosemo.observation.domain.activities import ActivityRecord
from mosemo.observation.domain.focus_sessions import FocusSession
from mosemo.observation.infrastructure.tables import activity_records, focus_sessions
from mosemo.platform.clock import utc_now
from mosemo.platform.database.engine import get_session
from mosemo.shared.presentation.current_account import get_current_account
from tests.support.batches import batch_client, post_activity
from tests.support.identity import (
    current_account_provider,
    delete_accounts,
    token_service,
)


@pytest_asyncio.fixture
async def focus_client(
    integration_database_url: str, identity_config: IdentityConfig
) -> AsyncIterator:
    engine = create_async_engine(integration_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_current_account] = current_account_provider(
        identity_config
    )

    async def session_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    async with factory() as session:
        account = Account(provider=AccountProvider.KAKAO, provider_subject=str(uuid4()))
        session.add(account)
        other = Account(provider=AccountProvider.KAKAO, provider_subject=str(uuid4()))
        session.add(other)
        await session.flush()
        device = Device(account_id=account.id, idempotency_key=uuid4())
        label = Label(account_id=account.id, display_name="집중 테스트")
        other_label = Label(account_id=other.id, display_name="다른 계정")
        session.add_all([device, label, other_label])
        await session.commit()
        headers = {
            "Authorization": "Bearer "
            + token_service(identity_config).issue_access_token(
                account.id, now=utc_now()
            )
        }
        other_headers = {
            "Authorization": "Bearer "
            + token_service(identity_config).issue_access_token(other.id, now=utc_now())
        }
    async with batch_client(app, factory) as client:
        yield (
            client,
            factory,
            headers,
            other_headers,
            device.id,
            label.id,
            other_label.id,
        )
    async with factory() as session:
        await session.execute(
            delete(ActivityRecord).where(activity_records.c.device_id == device.id)
        )
        await session.execute(
            delete(FocusSession).where(
                focus_sessions.c.account_id.in_([account.id, other.id])
            )
        )
        await session.execute(delete_accounts(account.id, other.id))
        await session.commit()
    await engine.dispose()


def start_request(device_id, *, target=0):
    return {
        "id": str(uuid4()),
        "deviceId": str(device_id),
        "startedAt": "2026-10-07T00:00:00.123456Z",
        "targetSeconds": target,
    }


def completion(label_id, **changes):
    return {
        "endedAt": "2026-10-07T00:20:00.123456Z",
        "workSeconds": 1100,
        "labelId": str(label_id),
        "description": "",
        **changes,
    }


@pytest.mark.asyncio
async def test_start_observe_complete_retry_and_list(focus_client):
    client, factory, headers, other_headers, device_id, label_id, _ = focus_client
    request = start_request(device_id, target=1200)
    url = "/api/v1/focus-sessions"
    created = await client.post(url, headers=headers, json=request)
    assert created.status_code == 201, created.text
    retry = await client.post(url, headers=headers, json=request)
    assert retry.json() == created.json()
    assert (
        await client.post(url, headers=headers, json={**request, "targetSeconds": 0})
    ).status_code == 409
    assert (await client.get(url + "?date=2026-10-07", headers=headers)).json() == []
    activity = {
        "deviceId": str(device_id),
        "eventId": str(uuid4()),
        "sequence": 0,
        "recordType": "activity_observation",
        "observedAt": "2026-10-07T00:10:00Z",
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "context": {"kind": "opaque"},
        "focusSessionId": request["id"],
    }
    assert (
        await post_activity(client, headers=headers, json=activity)
    ).status_code == 200
    async with factory() as session:
        stored = await session.scalar(
            select(ActivityRecord).where(
                activity_records.c.event_id == activity["eventId"]
            )
        )
        assert str(stored.focus_session_id) == request["id"]
    complete_url = url + "/" + request["id"] + "/completion"
    # A completion cannot move the session end before an already-linked observation.
    invalid_end = await client.put(
        complete_url,
        headers=headers,
        json=completion(
            label_id, endedAt="2026-10-07T00:05:00.123456Z", workSeconds=100
        ),
    )
    assert invalid_end.status_code == 422
    finished = await client.put(
        complete_url, headers=headers, json=completion(label_id)
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["workSeconds"] == 1100
    assert (
        await client.put(complete_url, headers=headers, json=completion(label_id))
    ).json() == finished.json()
    assert (
        await client.put(
            complete_url,
            headers=headers,
            json=completion(label_id, description="changed"),
        )
    ).status_code == 409
    assert (await client.get(url + "?date=2026-10-07", headers=headers)).json() == [
        finished.json()
    ]
    assert (
        await client.get(url + "?date=2026-10-07", headers=other_headers)
    ).json() == []
    assert (
        await client.put(complete_url, headers=other_headers, json=completion(label_id))
    ).status_code == 404
    # Delayed replay remains valid after completion and preserves idempotency.
    assert (
        await post_activity(client, headers=headers, json=activity)
    ).status_code == 200
    assert (
        await post_activity(
            client,
            headers=headers,
            json={**activity, "focusSessionId": None},
        )
    ).status_code == 200
    assert (await client.get(url + "?date=2026-10-07")).status_code == 401


@pytest.mark.asyncio
async def test_foreign_label_invalid_durations_and_activity_link_are_rejected(
    focus_client,
):
    client, _, headers, other_headers, device_id, label_id, other_label_id = (
        focus_client
    )
    request = start_request(device_id, target=1200)
    url = "/api/v1/focus-sessions"
    assert (
        await client.post(url, headers=other_headers, json=request)
    ).status_code == 404
    assert (await client.post(url, headers=headers, json=request)).status_code == 201
    complete_url = url + "/" + request["id"] + "/completion"
    assert (
        await client.put(complete_url, headers=headers, json=completion(other_label_id))
    ).status_code == 404
    for changes in (
        {"workSeconds": 1201},
        {"workSeconds": -1},
        {"endedAt": "2026-10-06T00:00:00Z"},
        {"labelId": None},
    ):
        result = await client.put(
            complete_url, headers=headers, json=completion(label_id, **changes)
        )
        assert result.status_code == 422, result.text
    activity = {
        "deviceId": str(device_id),
        "eventId": str(uuid4()),
        "sequence": 0,
        "recordType": "activity_observation",
        "observedAt": "2026-10-06T00:10:00Z",
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "context": {"kind": "opaque"},
        "focusSessionId": request["id"],
    }
    # Older clients without a session link continue to work.
    del activity["focusSessionId"]
    assert (
        await post_activity(client, headers=headers, json=activity)
    ).status_code == 200


@pytest.mark.asyncio
async def test_suspended_collection_record_rejects_focus_link(focus_client):
    client, factory, headers, _, device_id, _, _ = focus_client
    request = start_request(device_id)
    assert (
        await client.post("/api/v1/focus-sessions", headers=headers, json=request)
    ).status_code == 201
    activity = {
        "deviceId": str(device_id),
        "eventId": str(uuid4()),
        "sequence": 0,
        "recordType": "collection_state_changed",
        "observedAt": "2026-10-07T00:10:00Z",
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "state": "suspended",
        "reason": "user_paused",
        "focusSessionId": request["id"],
    }
    response = await post_activity(client, headers=headers, json=activity)
    assert response.status_code == 422
    async with factory() as session:
        assert (
            await session.scalar(
                select(ActivityRecord).where(
                    activity_records.c.event_id == activity["eventId"]
                )
            )
            is None
        )
    activity.pop("focusSessionId")
    assert (
        await post_activity(client, headers=headers, json=activity)
    ).status_code == 200


@pytest.mark.asyncio
async def test_completed_session_blocks_label_deletion_but_allows_account_cascade(
    focus_client,
):
    client, factory, headers, _, device_id, label_id, _ = focus_client
    request = start_request(device_id)
    assert (
        await client.post("/api/v1/focus-sessions", headers=headers, json=request)
    ).status_code == 201
    assert (
        await client.put(
            "/api/v1/focus-sessions/" + request["id"] + "/completion",
            headers=headers,
            json=completion(label_id),
        )
    ).status_code == 200
    async with factory() as session:
        label = await session.get(Label, label_id)
        account_id = label.account_id
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(delete(Label).where(label_table.c.id == label_id))
        await session.execute(delete_accounts(account_id))
        await session.commit()
        assert await session.get(Label, label_id, populate_existing=True) is None
        assert await session.get(FocusSession, request["id"]) is None


@pytest.mark.parametrize("invalid_link", ["time", "missing"])
@pytest.mark.asyncio
async def test_invalid_activity_focus_link_rolls_back_and_retains_original(
    focus_client, invalid_link
):
    client, factory, headers, _, device_id, _, _ = focus_client
    request = start_request(device_id)
    assert (
        await client.post("/api/v1/focus-sessions", headers=headers, json=request)
    ).status_code == 201
    activity = {
        "deviceId": str(device_id),
        "eventId": str(uuid4()),
        "sequence": 0,
        "recordType": "activity_observation",
        "observedAt": "2026-10-06T00:10:00Z",
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "context": {"kind": "opaque"},
        "focusSessionId": request["id"] if invalid_link == "time" else str(uuid4()),
    }
    response = await post_activity(
        client,
        headers=headers,
        json=activity,
        expected_failure="FocusSessionInvalidTimeError"
        if invalid_link == "time"
        else "FocusSessionNotFoundError",
    )
    assert response.status_code == (409 if invalid_link == "time" else 404)
    async with factory() as session:
        assert await session.get(ActivityRecord, activity["eventId"]) is None


@contextmanager
def completion_logs(caplog) -> Iterator[list[logging.LogRecord]]:
    # Alembic's fileConfig in the migration fixture disables existing loggers,
    # and logging configuration decides where `mosemo` records propagate, so
    # deliver the use case logger's records to caplog alone.
    logger = logging.getLogger("mosemo.observation.application.focus_sessions")
    saved = (logger.disabled, logger.propagate, logger.level)
    logger.disabled, logger.propagate = False, False
    logger.setLevel(logging.INFO)
    logger.addHandler(caplog.handler)
    try:
        yield caplog.records
    finally:
        logger.removeHandler(caplog.handler)
        logger.disabled, logger.propagate = saved[:2]
        logger.setLevel(saved[2])


def completed_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in records if r.getMessage() == "focus session completed"]


def pause(start: str, end: str) -> dict[str, str]:
    return {
        "startedAt": f"2026-10-07T00:{start}.123456Z",
        "endedAt": f"2026-10-07T00:{end}.123456Z",
    }


async def start_session(client, headers, device_id) -> str:
    request = start_request(device_id)
    response = await client.post(
        "/api/v1/focus-sessions", headers=headers, json=request
    )
    assert response.status_code == 201, response.text
    return "/api/v1/focus-sessions/" + request["id"] + "/completion"


@pytest.mark.parametrize(
    "pauses",
    [
        pytest.param([pause("10:00", "21:00")], id="after-session-end"),
        pytest.param(
            [
                {
                    "startedAt": "2026-10-06T23:59:00Z",
                    "endedAt": "2026-10-07T00:01:00Z",
                }
            ],
            id="before-session-start",
        ),
        pytest.param(
            [pause("02:00", "06:00"), pause("05:00", "08:00")], id="overlapping"
        ),
    ],
)
@pytest.mark.asyncio
async def test_completion_rejects_pauses_outside_the_session_or_overlapping(
    focus_client, pauses
):
    client, _, headers, _, device_id, label_id, _ = focus_client
    complete_url = await start_session(client, headers, device_id)

    response = await client.put(
        complete_url,
        headers=headers,
        json=completion(label_id, workSeconds=600, pauses=pauses),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_completion_stores_sorted_pauses_and_logs_the_work_gap(
    focus_client, caplog
):
    client, _, headers, _, device_id, label_id, _ = focus_client
    complete_url = await start_session(client, headers, device_id)
    pauses = [pause("10:00", "15:00"), pause("03:00", "03:00"), pause("01:00", "02:00")]

    with completion_logs(caplog) as records:
        # 20 minutes minus 6 paused minutes leaves 840 analysable seconds.
        finished = await client.put(
            complete_url,
            headers=headers,
            json=completion(label_id, workSeconds=830, pauses=pauses),
        )

    assert finished.status_code == 200, finished.text
    assert finished.json()["pauses"] == [
        pause("01:00", "02:00"),
        pause("10:00", "15:00"),
    ]
    listed = await client.get("/api/v1/focus-sessions?date=2026-10-07", headers=headers)
    assert listed.json() == [finished.json()]
    [record] = completed_records(records)
    assert record.__dict__["work_seconds_gap"] == 10
    assert record.__dict__["dropped_zero_pauses"] == 1

    caplog.clear()
    with completion_logs(caplog) as records:
        retry = await client.put(
            complete_url,
            headers=headers,
            json=completion(label_id, workSeconds=830, pauses=pauses[::-1]),
        )
    assert retry.json() == finished.json()
    assert completed_records(records) == []
    conflict = await client.put(
        complete_url,
        headers=headers,
        json=completion(label_id, workSeconds=830, pauses=pauses[:1]),
    )
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_completion_without_pauses_returns_null_and_zero_pauses_return_empty(
    focus_client,
):
    client, factory, headers, _, device_id, label_id, _ = focus_client
    without_info_url = await start_session(client, headers, device_id)
    zero_only_url = await start_session(client, headers, device_id)

    without_info = await client.put(
        without_info_url, headers=headers, json=completion(label_id)
    )
    zero_only = await client.put(
        zero_only_url,
        headers=headers,
        json=completion(label_id, pauses=[pause("05:00", "05:00")]),
    )

    assert without_info.status_code == 200, without_info.text
    assert without_info.json()["pauses"] is None
    assert zero_only.status_code == 200, zero_only.text
    assert zero_only.json()["pauses"] == []
    async with factory() as session:
        stored = await session.get(FocusSession, zero_only.json()["id"])
        assert stored.pauses_reported
        assert stored.pauses == []


@pytest.mark.asyncio
async def test_completion_accepts_work_two_seconds_over_the_session(focus_client):
    client, _, headers, _, device_id, label_id, _ = focus_client
    complete_url = await start_session(client, headers, device_id)

    # The database check allows the same 2-second tolerance as the aggregate.
    response = await client.put(
        complete_url, headers=headers, json=completion(label_id, workSeconds=1202)
    )

    assert response.status_code == 200, response.text
