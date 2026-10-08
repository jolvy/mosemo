from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.activities.models import ActivityRecord
from mosemo.api import v1_api_router
from mosemo.auth.tokens import TokenService
from mosemo.config import Config, get_config
from mosemo.database import get_session
from mosemo.devices.models import Device
from mosemo.exception_handlers import register_exception_handlers

ACTIVITIES_URL = "/api/v1/activities"
TIMELINE_URL = "/api/v1/activities/timeline?date=2026-09-14"


@pytest_asyncio.fixture
async def activity_api(
    integration_database_url: str,
    config: Config,
) -> AsyncIterator[
    tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession], list[UUID]]
]:
    engine = create_async_engine(integration_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    account_ids: list[UUID] = []

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_config] = lambda: config

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client, session_factory, account_ids
    finally:
        if account_ids:
            async with session_factory() as session:
                await session.execute(
                    delete(Account).where(Account.account_id.in_(account_ids))
                )
                await session.commit()
        await engine.dispose()


async def create_account_with_devices(
    session_factory: async_sessionmaker[AsyncSession],
    account_ids: list[UUID],
    *,
    device_count: int = 1,
) -> tuple[UUID, list[UUID]]:
    async with session_factory() as session:
        account = AccountRepository(session).save(
            provider=AccountProvider.KAKAO,
            provider_subject=f"offline-replay-{uuid4()}",
        )
        await session.flush()
        devices = [
            Device(account_id=account.account_id, idempotency_key=uuid4())
            for _ in range(device_count)
        ]
        session.add_all(devices)
        await session.flush()
        account_id = account.account_id
        device_ids = [device.device_id for device in devices]
        await session.commit()
    account_ids.append(account_id)
    return account_id, device_ids


def auth_headers(config: Config, account_id: UUID) -> dict[str, str]:
    token = TokenService(config.auth).issue_access_token(account_id)
    return {"Authorization": f"Bearer {token}"}


def activity_observation(
    device_id: UUID,
    *,
    sequence: int,
    observed_at: str,
    context: dict[str, object],
    event_id: UUID | None = None,
) -> dict[str, object]:
    return {
        "deviceId": str(device_id),
        "eventId": str(event_id or uuid4()),
        "sequence": sequence,
        "recordType": "activity_observation",
        "observedAt": observed_at,
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "context": context,
    }


def collection_state_changed(
    device_id: UUID,
    *,
    sequence: int,
    observed_at: str,
    state: str = "suspended",
) -> dict[str, object]:
    return {
        "deviceId": str(device_id),
        "eventId": str(uuid4()),
        "sequence": sequence,
        "recordType": "collection_state_changed",
        "observedAt": observed_at,
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "state": state,
        "reason": "screen_locked" if state == "suspended" else "screen_unlocked",
    }


def detailed_context(name: str = "Editor") -> dict[str, object]:
    return {
        "kind": "detailed",
        "app": {
            "bundleId": {"status": "captured", "value": "com.example.editor"},
            "name": {"status": "captured", "value": name},
        },
        "window": {"status": "absent"},
        "web": {"kind": "not_applicable"},
    }


def offline_records(device_id: UUID) -> list[dict[str, object]]:
    return [
        activity_observation(
            device_id,
            sequence=10,
            observed_at="2026-09-14T00:00:00Z",
            context={"kind": "opaque"},
        ),
        collection_state_changed(
            device_id, sequence=30, observed_at="2026-09-14T00:00:30Z"
        ),
        collection_state_changed(
            device_id,
            sequence=50,
            observed_at="2026-09-14T00:00:40Z",
            state="active",
        ),
        activity_observation(
            device_id,
            sequence=90,
            observed_at="2026-09-14T00:00:50Z",
            context=detailed_context(),
        ),
    ]


def timeline_meaning(segments: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {key: value for key, value in segment.items() if key != "segmentId"}
        for segment in segments
    ]


async def stored_count(
    session_factory: async_sessionmaker[AsyncSession], device_id: UUID
) -> int:
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ActivityRecord)
            .where(ActivityRecord.device_id == device_id)
        )
    assert count is not None
    return count


@pytest.mark.asyncio
async def test_delayed_offline_replays_converge_with_chronological_http_timeline(
    activity_api: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], list[UUID]
    ],
    config: Config,
) -> None:
    client, session_factory, account_ids = activity_api
    ordered_account, [ordered_device] = await create_account_with_devices(
        session_factory, account_ids
    )
    delayed_account, [delayed_device] = await create_account_with_devices(
        session_factory, account_ids
    )
    ordered_headers = auth_headers(config, ordered_account)
    delayed_headers = auth_headers(config, delayed_account)
    ordered = offline_records(ordered_device)
    delayed = offline_records(delayed_device)

    for record in ordered:
        response = await client.post(
            ACTIVITIES_URL, headers=ordered_headers, json=record
        )
        assert response.status_code == 201
        assert response.json()["eventId"] == record["eventId"]
        assert response.json()["status"] == "accepted"
    first_responses: dict[int, dict[str, object]] = {}
    for index in (3, 1, 0, 2):
        response = await client.post(
            ACTIVITIES_URL, headers=delayed_headers, json=delayed[index]
        )
        assert response.status_code == 201
        assert response.json()["eventId"] == delayed[index]["eventId"]
        assert response.json()["status"] == "accepted"
        first_responses[index] = response.json()

    before_retry = await client.get(TIMELINE_URL, headers=delayed_headers)
    assert before_retry.status_code == 200
    for index in (0, 1, 3):
        response = await client.post(
            ACTIVITIES_URL, headers=delayed_headers, json=delayed[index]
        )
        assert response.status_code == 201
        assert response.json() == first_responses[index]
        assert response.json()["status"] == "accepted"

    ordered_timeline = await client.get(TIMELINE_URL, headers=ordered_headers)
    delayed_timeline = await client.get(TIMELINE_URL, headers=delayed_headers)
    assert ordered_timeline.status_code == delayed_timeline.status_code == 200
    assert delayed_timeline.json() == before_retry.json()
    assert timeline_meaning(delayed_timeline.json()) == timeline_meaning(
        ordered_timeline.json()
    )
    assert timeline_meaning(delayed_timeline.json()) == [
        {
            "segmentType": "activity",
            "startedAt": "2026-09-14T00:00:00Z",
            "endedAt": "2026-09-14T00:00:30Z",
            "lastObservedAt": "2026-09-14T00:00:00Z",
            "context": {"kind": "opaque"},
        },
        {
            "segmentType": "capture_gap",
            "startedAt": "2026-09-14T00:00:30Z",
            "endedAt": "2026-09-14T00:00:50Z",
            "reason": "screen_locked",
        },
        {
            "segmentType": "activity",
            "startedAt": "2026-09-14T00:00:50Z",
            "endedAt": "2026-09-14T00:00:50Z",
            "lastObservedAt": "2026-09-14T00:00:50Z",
            "context": detailed_context(),
        },
    ]
    assert await stored_count(session_factory, ordered_device) == 4
    assert await stored_count(session_factory, delayed_device) == 4


@pytest.mark.parametrize(
    "scenario",
    [
        "event_sequence_changed",
        "event_body_changed",
        "event_observed_at_changed",
        "event_device_changed",
        "device_sequence_reused",
        "negative_sequence",
        "invalid_context",
        "missing_auth",
        "invalid_auth",
        "foreign_device",
        "unregistered_device",
    ],
)
@pytest.mark.asyncio
async def test_rejected_activity_preserves_original_and_http_timeline(
    activity_api: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], list[UUID]
    ],
    config: Config,
    scenario: str,
) -> None:
    client, session_factory, account_ids = activity_api
    account_id, [device_id, other_device_id] = await create_account_with_devices(
        session_factory, account_ids, device_count=2
    )
    foreign_account, _ = await create_account_with_devices(session_factory, account_ids)
    headers = auth_headers(config, account_id)
    original = activity_observation(
        device_id,
        sequence=10,
        observed_at="2026-09-14T00:00:00Z",
        context={"kind": "opaque"},
    )
    accepted = await client.post(ACTIVITIES_URL, headers=headers, json=original)
    assert accepted.status_code == 201
    before = await client.get(TIMELINE_URL, headers=headers)
    assert before.status_code == 200
    async with session_factory() as session:
        stored_before = await session.get(
            ActivityRecord, UUID(str(original["eventId"]))
        )
    assert stored_before is not None
    original_fields = (
        stored_before.device_id,
        stored_before.sequence,
        stored_before.record_type,
        stored_before.observed_at,
        stored_before.timezone_id,
        stored_before.utc_offset_minutes,
        stored_before.payload,
        stored_before.received_at,
    )

    new_event_id = str(uuid4())
    changes: dict[str, dict[str, object]] = {
        "event_sequence_changed": {"sequence": 11},
        "event_body_changed": {"context": detailed_context()},
        "event_observed_at_changed": {"observedAt": "2026-09-14T00:00:01Z"},
        "event_device_changed": {"deviceId": str(other_device_id)},
        "device_sequence_reused": {"eventId": new_event_id},
        "negative_sequence": {"eventId": new_event_id, "sequence": -1},
        "invalid_context": {"eventId": new_event_id, "context": {"kind": "detailed"}},
        "missing_auth": {"eventId": new_event_id},
        "invalid_auth": {"eventId": new_event_id},
        "foreign_device": {"eventId": new_event_id},
        "unregistered_device": {"eventId": new_event_id, "deviceId": str(uuid4())},
    }
    expected_errors = {
        "event_sequence_changed": (409, "ACTIVITY_EVENT_ID_CONFLICT"),
        "event_body_changed": (409, "ACTIVITY_EVENT_ID_CONFLICT"),
        "event_observed_at_changed": (409, "ACTIVITY_EVENT_ID_CONFLICT"),
        "event_device_changed": (409, "ACTIVITY_EVENT_ID_CONFLICT"),
        "device_sequence_reused": (409, "ACTIVITY_SEQUENCE_CONFLICT"),
        "negative_sequence": (422, "INVALID_ARGUMENT"),
        "invalid_context": (422, "INVALID_ARGUMENT"),
        "missing_auth": (401, "AUTH_INVALID_ACCESS_TOKEN"),
        "invalid_auth": (401, "AUTH_INVALID_ACCESS_TOKEN"),
        "foreign_device": (404, "ACTIVITY_DEVICE_NOT_FOUND"),
        "unregistered_device": (404, "ACTIVITY_DEVICE_NOT_FOUND"),
    }
    request_headers = {
        "missing_auth": {},
        "invalid_auth": {"Authorization": "Bearer invalid-token"},
        "foreign_device": auth_headers(config, foreign_account),
    }.get(scenario, headers)
    status_code, error_status = expected_errors[scenario]
    response = await client.post(
        ACTIVITIES_URL, headers=request_headers, json=original | changes[scenario]
    )
    assert response.status_code == status_code
    error = response.json()["error"]
    assert error["status"] == error_status
    assert error["code"] == status_code
    assert isinstance(error["message"], str) and error["message"]
    assert bool(error["details"]) is (status_code == 422)
    if status_code == 401:
        assert response.headers["www-authenticate"] == "Bearer"

    after = await client.get(TIMELINE_URL, headers=headers)
    assert after.status_code == 200
    assert after.json() == before.json()
    assert await stored_count(session_factory, device_id) == 1
    assert await stored_count(session_factory, other_device_id) == 0
    async with session_factory() as session:
        stored_after = await session.get(ActivityRecord, UUID(str(original["eventId"])))
    assert stored_after is not None
    assert (
        stored_after.device_id,
        stored_after.sequence,
        stored_after.record_type,
        stored_after.observed_at,
        stored_after.timezone_id,
        stored_after.utc_offset_minutes,
        stored_after.payload,
        stored_after.received_at,
    ) == original_fields


@pytest.mark.asyncio
async def test_observed_then_received_order_ignores_device_sequence_and_event_id(
    activity_api: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], list[UUID]
    ],
    config: Config,
) -> None:
    client, session_factory, account_ids = activity_api
    account_id, [first_device, second_device] = await create_account_with_devices(
        session_factory, account_ids, device_count=2
    )
    headers = auth_headers(config, account_id)
    low_event_id, high_event_id = sorted((uuid4(), uuid4()))
    later_observation = activity_observation(
        first_device,
        sequence=100,
        observed_at="2026-09-14T00:00:20Z",
        context={"kind": "opaque"},
    )
    earlier_received = activity_observation(
        first_device,
        sequence=5,
        observed_at="2026-09-14T00:00:00Z",
        context=detailed_context("Editor"),
        event_id=high_event_id,
    )
    later_received = activity_observation(
        second_device,
        sequence=1,
        observed_at="2026-09-14T00:00:00Z",
        context=detailed_context("Mail"),
        event_id=low_event_id,
    )
    for record in (later_observation, earlier_received, later_received):
        response = await client.post(ACTIVITIES_URL, headers=headers, json=record)
        assert response.status_code == 201

    async with session_factory() as session:
        first_stored = await session.get(ActivityRecord, high_event_id)
        second_stored = await session.get(ActivityRecord, low_event_id)
    assert first_stored is not None and second_stored is not None
    assert first_stored.received_at < second_stored.received_at

    timeline = await client.get(TIMELINE_URL, headers=headers)
    assert timeline.status_code == 200
    assert [segment["startedAt"] for segment in timeline.json()] == [
        "2026-09-14T00:00:00Z",
        "2026-09-14T00:00:00Z",
        "2026-09-14T00:00:20Z",
    ]
    assert [segment["context"] for segment in timeline.json()] == [
        detailed_context("Editor"),
        detailed_context("Mail"),
        {"kind": "opaque"},
    ]


@pytest.mark.asyncio
async def test_event_id_orders_equal_observation_and_receive_times_over_http(
    activity_api: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], list[UUID]
    ],
    config: Config,
) -> None:
    client, session_factory, account_ids = activity_api
    account_id, [device_id] = await create_account_with_devices(
        session_factory, account_ids
    )
    headers = auth_headers(config, account_id)
    low_event_id, high_event_id = sorted((uuid4(), uuid4()))
    high = activity_observation(
        device_id,
        sequence=1,
        observed_at="2026-09-14T00:00:00Z",
        context=detailed_context("Editor"),
        event_id=high_event_id,
    )
    low = activity_observation(
        device_id,
        sequence=100,
        observed_at="2026-09-14T00:00:00Z",
        context=detailed_context("Mail"),
        event_id=low_event_id,
    )
    for record in (high, low):
        response = await client.post(ACTIVITIES_URL, headers=headers, json=record)
        assert response.status_code == 201

    async with session_factory() as session:
        await session.execute(
            update(ActivityRecord)
            .where(ActivityRecord.event_id.in_((low_event_id, high_event_id)))
            .values(received_at=datetime(2026, 9, 14, tzinfo=UTC))
        )
        await session.commit()

    replay_trigger = activity_observation(
        device_id,
        sequence=500,
        observed_at="2026-09-14T00:00:00Z",
        context={"kind": "opaque"},
    )
    response = await client.post(ACTIVITIES_URL, headers=headers, json=replay_trigger)
    assert response.status_code == 201
    timeline = await client.get(TIMELINE_URL, headers=headers)
    assert timeline.status_code == 200
    assert [segment["context"] for segment in timeline.json()] == [
        detailed_context("Mail"),
        detailed_context("Editor"),
        {"kind": "opaque"},
    ]
