from collections.abc import AsyncIterator
from uuid import uuid4

import httpx2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.activities.models import ActivityRecord
from mosemo.activity_labels.catalog.models import Label
from mosemo.api import v1_api_router
from mosemo.auth.tokens import TokenService
from mosemo.config import Config, get_config
from mosemo.database import get_session
from mosemo.devices.models import Device
from mosemo.exception_handlers import register_exception_handlers
from mosemo.focus_sessions.models import FocusSession


@pytest_asyncio.fixture
async def focus_client(integration_database_url: str, config: Config) -> AsyncIterator:
    engine = create_async_engine(integration_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_config] = lambda: config

    async def session_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    async with factory() as session:
        account = AccountRepository(session).save(
            provider=AccountProvider.KAKAO, provider_subject=str(uuid4())
        )
        other = AccountRepository(session).save(
            provider=AccountProvider.KAKAO, provider_subject=str(uuid4())
        )
        await session.flush()
        device = Device(account_id=account.account_id, idempotency_key=uuid4())
        label = Label(account_id=account.account_id, display_name="집중 테스트")
        other_label = Label(account_id=other.account_id, display_name="다른 계정")
        session.add_all([device, label, other_label])
        await session.commit()
        headers = {
            "Authorization": "Bearer "
            + TokenService(config.auth).issue_access_token(account.account_id)
        }
        other_headers = {
            "Authorization": "Bearer "
            + TokenService(config.auth).issue_access_token(other.account_id)
        }
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield (
            client,
            factory,
            headers,
            other_headers,
            device.device_id,
            label.label_id,
            other_label.label_id,
        )
    async with factory() as session:
        await session.execute(
            delete(ActivityRecord).where(ActivityRecord.device_id == device.device_id)
        )
        await session.execute(
            delete(FocusSession).where(
                FocusSession.account_id.in_([account.account_id, other.account_id])
            )
        )
        await session.execute(
            delete(Account).where(
                Account.account_id.in_([account.account_id, other.account_id])
            )
        )
        await session.commit()
    await engine.dispose()


def start_request(device_id, *, target=0):
    return {
        "sessionId": str(uuid4()),
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
        "focusSessionId": request["sessionId"],
    }
    assert (
        await client.post("/api/v1/activities", headers=headers, json=activity)
    ).status_code == 201
    async with factory() as session:
        stored = await session.scalar(
            select(ActivityRecord).where(ActivityRecord.event_id == activity["eventId"])
        )
        assert str(stored.focus_session_id) == request["sessionId"]
    complete_url = url + "/" + request["sessionId"] + "/completion"
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
        await client.post("/api/v1/activities", headers=headers, json=activity)
    ).status_code == 201
    assert (
        await client.post(
            "/api/v1/activities",
            headers=headers,
            json={**activity, "focusSessionId": None},
        )
    ).status_code == 409
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
    complete_url = url + "/" + request["sessionId"] + "/completion"
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
        "focusSessionId": request["sessionId"],
    }
    assert (
        await client.post("/api/v1/activities", headers=headers, json=activity)
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/activities",
            headers=headers,
            json={**activity, "focusSessionId": str(uuid4())},
        )
    ).status_code == 404
    # Older clients without a session link continue to work.
    del activity["focusSessionId"]
    assert (
        await client.post("/api/v1/activities", headers=headers, json=activity)
    ).status_code == 201
