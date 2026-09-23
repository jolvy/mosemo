import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.api import v1_api_router
from mosemo.auth.tokens import TokenService
from mosemo.config import Config, get_config
from mosemo.database import get_session
from mosemo.dependencies import get_clock
from mosemo.devices.models import Device
from mosemo.exception_handlers import register_exception_handlers
from mosemo.labels.models import Label
from mosemo.labels.repository import LabelRepository
from mosemo.labels.schemas import SEGMENT_VERSION_PATTERN


def detailed_context(name: str) -> dict[str, object]:
    return {
        "kind": "detailed",
        "app": {
            "bundleId": {"status": "captured", "value": "com.example.app"},
            "name": {"status": "captured", "value": name},
        },
        "window": {"status": "absent"},
        "web": {"kind": "not_applicable"},
    }


def activity_request(
    *,
    device_id: UUID,
    sequence: int,
    observed_at: str,
    context: Mapping[str, object],
) -> dict[str, object]:
    return {
        "deviceId": str(device_id),
        "eventId": str(uuid4()),
        "sequence": sequence,
        "recordType": "activity_observation",
        "observedAt": observed_at,
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "context": context,
    }


def state_change_request(
    *,
    device_id: UUID,
    sequence: int,
    observed_at: str,
) -> dict[str, object]:
    return {
        "deviceId": str(device_id),
        "eventId": str(uuid4()),
        "sequence": sequence,
        "recordType": "collection_state_changed",
        "observedAt": observed_at,
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "state": "suspended",
        "reason": "screen_locked",
    }


def auth_headers(config: Config, account_id: UUID) -> dict[str, str]:
    token = TokenService(config.auth).issue_access_token(account_id)
    return {"Authorization": f"Bearer {token}"}


async def create_account_with_labels(
    session: AsyncSession,
) -> tuple[UUID, Device, UUID, UUID]:
    account = AccountRepository(session).save(
        provider=AccountProvider.KAKAO,
        provider_subject=f"label-confirmation-{uuid4()}",
    )
    await session.flush()
    device = Device(account_id=account.account_id, idempotency_key=uuid4())
    session.add(device)
    await session.flush()
    labels = LabelRepository(session).create_defaults(account_id=account.account_id)
    await session.flush()
    return account.account_id, device, labels[0].label_id, labels[1].label_id


@pytest_asyncio.fixture
async def label_api_client(
    integration_database_url: str,
    config: Config,
) -> AsyncIterator[
    tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI]
]:
    engine = create_async_engine(integration_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_config] = lambda: config

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client, session_factory, app
    await engine.dispose()


@pytest.mark.asyncio
async def test_label_state_can_be_confirmed_and_corrected_over_http(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, coding_id, learning_id = await create_account_with_labels(
            session
        )
        await session.commit()

    headers = auth_headers(config, account_id)

    try:
        first = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=1,
                observed_at="2026-09-14T00:00:00Z",
                context=detailed_context("Editor"),
            ),
        )
        assert first.status_code == 201
        second = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=2,
                observed_at="2026-09-14T00:00:30Z",
                context={"kind": "opaque"},
            ),
        )
        assert second.status_code == 201

        timeline = await client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers=headers,
        )
        assert timeline.status_code == 200
        segment = next(
            item
            for item in timeline.json()
            if item["context"].get("kind") == "detailed"
        )
        segment_id = segment["segmentId"]

        pending = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state",
            headers=headers,
        )
        assert pending.status_code == 200
        pending_body = pending.json()
        assert pending_body["state"] == "pending"
        assert len(pending_body["segmentVersion"]) == 64
        assert re.fullmatch(SEGMENT_VERSION_PATTERN, pending_body["segmentVersion"])

        confirmed = await client.put(
            f"/api/v1/activities/segments/{segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": pending_body["segmentVersion"],
                "selection": {"kind": "label", "labelId": str(coding_id)},
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["selection"] == {
            "kind": "label",
            "labelId": str(coding_id),
        }

        corrected = await client.put(
            f"/api/v1/activities/segments/{segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": pending_body["segmentVersion"],
                "selection": {"kind": "unclassified"},
            },
        )
        assert corrected.status_code == 200
        assert corrected.json()["selection"] == {"kind": "unclassified"}

        latest = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state",
            headers=headers,
        )
        assert latest.status_code == 200
        assert latest.json()["selection"] == {"kind": "unclassified"}

        corrected_label = await client.put(
            f"/api/v1/activities/segments/{segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": pending_body["segmentVersion"],
                "selection": {"kind": "label", "labelId": str(learning_id)},
            },
        )
        assert corrected_label.status_code == 200
        assert corrected_label.json()["selection"] == {
            "kind": "label",
            "labelId": str(learning_id),
        }
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_label_confirmation_enforces_ownership_and_eligibility(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, coding_id, _ = await create_account_with_labels(session)
        other_account_id, other_device, _, _ = await create_account_with_labels(session)
        await session.commit()

    headers = auth_headers(config, account_id)
    other_headers = auth_headers(config, other_account_id)
    account_ids = (account_id, other_account_id)
    try:
        await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=1,
                observed_at="2026-09-14T00:00:00Z",
                context=detailed_context("Detailed"),
            ),
        )
        closed = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=2,
                observed_at="2026-09-14T00:00:30Z",
                context={"kind": "opaque"},
            ),
        )
        assert closed.status_code == 201
        timeline = await client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers=headers,
        )
        detailed_segment = next(
            item
            for item in timeline.json()
            if item["context"].get("kind") == "detailed"
        )
        segment_id = detailed_segment["segmentId"]
        state = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state",
            headers=headers,
        )
        version = state.json()["segmentVersion"]

        unowned = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state",
            headers=other_headers,
        )
        assert unowned.status_code == 404

        archived_at = datetime.now(UTC)
        async with session_factory() as session:
            archived_label = await session.get(Label, coding_id)
            assert archived_label is not None
            archived_label.archived_at = archived_at
            await session.commit()
        archived = await client.put(
            f"/api/v1/activities/segments/{segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": version,
                "selection": {"kind": "label", "labelId": str(coding_id)},
            },
        )
        assert archived.status_code == 404
        assert archived.json()["error"]["status"] == "LABEL_NOT_AVAILABLE"

        now = datetime.now(UTC).replace(microsecond=0)
        open_segment = await client.post(
            "/api/v1/activities",
            headers=other_headers,
            json=activity_request(
                device_id=other_device.device_id,
                sequence=1,
                observed_at=now.isoformat().replace("+00:00", "Z"),
                context=detailed_context("Open"),
            ),
        )
        assert open_segment.status_code == 201
        open_timeline = await client.get(
            f"/api/v1/activities/timeline?date={now.date().isoformat()}",
            headers=other_headers,
        )
        open_id = open_timeline.json()[0]["segmentId"]
        open_state = await client.get(
            f"/api/v1/activities/segments/{open_id}/label-state",
            headers=other_headers,
        )
        assert open_state.status_code == 409
        assert open_state.json()["error"]["status"] == (
            "ACTIVITY_SEGMENT_NOT_LABELABLE"
        )

        opaque = await client.post(
            "/api/v1/activities",
            headers=other_headers,
            json=activity_request(
                device_id=other_device.device_id,
                sequence=2,
                observed_at="2026-09-14T00:00:30Z",
                context={"kind": "opaque"},
            ),
        )
        assert opaque.status_code == 201
        opaque_timeline = await client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers=other_headers,
        )
        opaque_id = next(
            item["segmentId"]
            for item in opaque_timeline.json()
            if item.get("context", {}).get("kind") == "opaque"
        )
        opaque_state = await client.get(
            f"/api/v1/activities/segments/{opaque_id}/label-state",
            headers=other_headers,
        )
        assert opaque_state.status_code == 409

        gap = await client.post(
            "/api/v1/activities",
            headers=other_headers,
            json=state_change_request(
                device_id=other_device.device_id,
                sequence=3,
                observed_at="2026-09-14T00:01:00Z",
            ),
        )
        assert gap.status_code == 201
        gap_timeline = await client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers=other_headers,
        )
        gap_id = next(
            item["segmentId"]
            for item in gap_timeline.json()
            if item["segmentType"] == "capture_gap"
        )
        gap_state = await client.get(
            f"/api/v1/activities/segments/{gap_id}/label-state",
            headers=other_headers,
        )
        assert gap_state.status_code == 409
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id.in_(account_ids))
            )
            await session.commit()


@pytest.mark.asyncio
async def test_label_timeline_groups_adjacent_segments_with_same_confirmation(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, coding_id, _ = await create_account_with_labels(session)
        coding_name = await session.scalar(
            select(Label.display_name).where(Label.label_id == coding_id)
        )
        await session.commit()

    headers = auth_headers(config, account_id)
    try:
        for sequence, observed_at, context in [
            (1, "2026-09-14T00:00:00Z", detailed_context("Editor")),
            (2, "2026-09-14T00:00:30Z", detailed_context("Browser")),
            (3, "2026-09-14T00:01:00Z", {"kind": "opaque"}),
        ]:
            response = await client.post(
                "/api/v1/activities",
                headers=headers,
                json=activity_request(
                    device_id=device.device_id,
                    sequence=sequence,
                    observed_at=observed_at,
                    context=context,
                ),
            )
            assert response.status_code == 201

        observation_timeline = await client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers=headers,
        )
        assert observation_timeline.status_code == 200
        detailed_segments = [
            segment
            for segment in observation_timeline.json()
            if segment.get("context", {}).get("kind") == "detailed"
        ]
        assert len(detailed_segments) == 2

        segment_versions = []
        for segment in detailed_segments:
            pending = await client.get(
                f"/api/v1/activities/segments/{segment['segmentId']}/label-state",
                headers=headers,
            )
            assert pending.status_code == 200
            segment_versions.append(pending.json()["segmentVersion"])
            confirmed = await client.put(
                f"/api/v1/activities/segments/{segment['segmentId']}/label-confirmation",
                headers=headers,
                json={
                    "segmentVersion": pending.json()["segmentVersion"],
                    "selection": {"kind": "label", "labelId": str(coding_id)},
                },
            )
            assert confirmed.status_code == 200

        response = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json() == [
            {
                "itemType": "activity_group",
                "startedAt": "2026-09-14T00:00:00Z",
                "endedAt": "2026-09-14T00:01:00Z",
                "state": "confirmed",
                "selection": {
                    "kind": "label",
                    "labelId": str(coding_id),
                    "displayName": coding_name,
                },
                "segments": [
                    {
                        "segmentId": segment["segmentId"],
                        "segmentVersion": segment_version,
                        "startedAt": segment["startedAt"],
                        "endedAt": segment["endedAt"],
                        "lastObservedAt": segment["lastObservedAt"],
                        "context": segment["context"],
                    }
                    for segment, segment_version in zip(
                        detailed_segments, segment_versions, strict=True
                    )
                ],
            },
            {
                "itemType": "opaque_activity",
                "segmentId": observation_timeline.json()[2]["segmentId"],
                "startedAt": "2026-09-14T00:01:00Z",
                "endedAt": "2026-09-14T00:01:00Z",
                "lastObservedAt": "2026-09-14T00:01:00Z",
                "context": {"kind": "opaque"},
            },
        ]
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_label_timeline_preserves_state_and_label_boundaries(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, coding_id, learning_id = await create_account_with_labels(
            session
        )
        await session.commit()

    headers = auth_headers(config, account_id)
    try:
        contexts = [
            "Editor",
            "Browser",
            "Terminal",
            "Editor",
            "Browser",
            "Terminal",
            "Notes",
        ]
        observed_at_values = [
            "2026-09-14T00:00:00Z",
            "2026-09-14T00:00:30Z",
            "2026-09-14T00:01:00Z",
            "2026-09-14T00:01:30Z",
            "2026-09-14T00:02:00Z",
            "2026-09-14T00:03:01Z",
            "2026-09-14T00:03:31Z",
        ]
        for sequence, name in enumerate(contexts, start=1):
            response = await client.post(
                "/api/v1/activities",
                headers=headers,
                json=activity_request(
                    device_id=device.device_id,
                    sequence=sequence,
                    observed_at=observed_at_values[sequence - 1],
                    context=detailed_context(name),
                ),
            )
            assert response.status_code == 201
        opaque = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=8,
                observed_at="2026-09-14T00:04:01Z",
                context={"kind": "opaque"},
            ),
        )
        assert opaque.status_code == 201

        observations = await client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers=headers,
        )
        segments = [
            item
            for item in observations.json()
            if item.get("context", {}).get("kind") == "detailed"
        ]
        assert len(segments) == 7

        for index, segment in enumerate(segments):
            if index in {0, 1, 2, 6}:
                label_id = learning_id if index == 1 else coding_id
                state = await client.get(
                    f"/api/v1/activities/segments/{segment['segmentId']}/label-state",
                    headers=headers,
                )
                confirmed = await client.put(
                    f"/api/v1/activities/segments/{segment['segmentId']}/label-confirmation",
                    headers=headers,
                    json={
                        "segmentVersion": state.json()["segmentVersion"],
                        "selection": (
                            {"kind": "unclassified"}
                            if index == 6
                            else {"kind": "label", "labelId": str(label_id)}
                        ),
                    },
                )
                assert confirmed.status_code == 200

        response = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert [item["itemType"] for item in body] == [
            "activity_group",
            "activity_group",
            "activity_group",
            "activity_group",
            "activity_group",
            "activity_group",
            "opaque_activity",
        ]
        assert [item["state"] for item in body[:6]] == [
            "confirmed",
            "confirmed",
            "confirmed",
            "pending",
            "pending",
            "confirmed",
        ]
        assert [
            [member["segmentId"] for member in item["segments"]] for item in body[:6]
        ] == [
            [segments[0]["segmentId"]],
            [segments[1]["segmentId"]],
            [segments[2]["segmentId"]],
            [segments[3]["segmentId"], segments[4]["segmentId"]],
            [segments[5]["segmentId"]],
            [segments[6]["segmentId"]],
        ]
        assert body[2]["selection"] == body[0]["selection"]
        assert body[5]["selection"] == {"kind": "unclassified"}
        assert body[3]["endedAt"] < body[4]["startedAt"]
        assert [
            member["context"]["app"]["name"]["value"] for member in body[3]["segments"]
        ] == ["Editor", "Browser"]
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_label_timeline_keeps_zero_duration_order_and_capture_gap_boundary(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, coding_id, _ = await create_account_with_labels(session)
        await session.commit()

    headers = auth_headers(config, account_id)
    try:
        observations = [
            (1, "2026-09-14T00:00:00Z", detailed_context("Editor")),
            (2, "2026-09-14T00:00:30Z", detailed_context("Browser")),
            (3, "2026-09-14T00:00:30Z", detailed_context("Editor")),
        ]
        for sequence, observed_at, context in observations:
            response = await client.post(
                "/api/v1/activities",
                headers=headers,
                json=activity_request(
                    device_id=device.device_id,
                    sequence=sequence,
                    observed_at=observed_at,
                    context=context,
                ),
            )
            assert response.status_code == 201
        suspended = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=state_change_request(
                device_id=device.device_id,
                sequence=4,
                observed_at="2026-09-14T00:01:00Z",
            ),
        )
        assert suspended.status_code == 201
        resumed = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=5,
                observed_at="2026-09-14T00:02:00Z",
                context=detailed_context("Terminal"),
            ),
        )
        assert resumed.status_code == 201

        observed = await client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers=headers,
        )
        segments = observed.json()
        activities = [
            segment
            for segment in segments
            if segment.get("context", {}).get("kind") == "detailed"
        ]
        assert len(activities) == 4
        for index, segment in enumerate(activities):
            state = await client.get(
                f"/api/v1/activities/segments/{segment['segmentId']}/label-state",
                headers=headers,
            )
            if index == 1:
                confirmation = await client.put(
                    f"/api/v1/activities/segments/{segment['segmentId']}/label-confirmation",
                    headers=headers,
                    json={
                        "segmentVersion": state.json()["segmentVersion"],
                        "selection": {"kind": "unclassified"},
                    },
                )
            else:
                confirmation = await client.put(
                    f"/api/v1/activities/segments/{segment['segmentId']}/label-confirmation",
                    headers=headers,
                    json={
                        "segmentVersion": state.json()["segmentVersion"],
                        "selection": {"kind": "label", "labelId": str(coding_id)},
                    },
                )
            assert confirmation.status_code == 200

        response = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert [item["itemType"] for item in body] == [
            "activity_group",
            "activity_group",
            "activity_group",
            "capture_gap",
            "activity_group",
        ]
        assert [
            member["context"]["app"]["name"]["value"]
            for item in body
            if item["itemType"] == "activity_group"
            for member in item["segments"]
        ] == ["Editor", "Browser", "Editor", "Terminal"]
        assert body[1]["startedAt"] == body[1]["endedAt"]
        assert body[1]["selection"] == {"kind": "unclassified"}
        assert body[0]["selection"] == body[2]["selection"] == body[4]["selection"]
        assert body[3]["startedAt"] == "2026-09-14T00:01:00Z"
        assert body[3]["endedAt"] == "2026-09-14T00:02:00Z"
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_label_timeline_defaults_to_account_local_today(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, app = label_api_client
    fixed_now = datetime(2026, 9, 14, 15, 30, tzinfo=UTC)
    app.dependency_overrides[get_clock] = lambda: lambda: fixed_now
    async with session_factory() as session:
        account_id, device, _, _ = await create_account_with_labels(session)
        other_account_id, _, _, _ = await create_account_with_labels(session)
        await session.commit()

    headers = auth_headers(config, account_id)
    other_headers = auth_headers(config, other_account_id)
    account_ids = (account_id, other_account_id)
    try:
        for sequence, observed_at, context in [
            (1, "2026-09-14T14:50:00Z", detailed_context("Before midnight")),
            (2, "2026-09-14T15:10:00Z", detailed_context("After midnight")),
            (3, "2026-09-14T15:11:00Z", {"kind": "opaque"}),
        ]:
            response = await client.post(
                "/api/v1/activities",
                headers=headers,
                json=activity_request(
                    device_id=device.device_id,
                    sequence=sequence,
                    observed_at=observed_at,
                    context=context,
                ),
            )
            assert response.status_code == 201

        today = await client.get(
            "/api/v1/activities/label-timeline",
            headers=headers,
        )
        yesterday = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        future = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-16",
            headers=headers,
        )
        invalid = await client.get(
            "/api/v1/activities/label-timeline?date=not-a-date",
            headers=headers,
        )
        unauthorized = await client.get("/api/v1/activities/label-timeline")
        isolated = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-15",
            headers=other_headers,
        )

        assert today.status_code == yesterday.status_code == future.status_code == 200
        assert today.json()[0]["itemType"] == "activity_group"
        assert today.json()[0]["startedAt"] == "2026-09-14T15:10:00Z"
        assert today.json()[0]["segments"][0]["context"]["app"]["name"]["value"] == (
            "After midnight"
        )
        assert len(yesterday.json()) == 1
        yesterday_group = yesterday.json()[0]
        assert yesterday_group["itemType"] == "activity_group"
        assert yesterday_group["startedAt"] == "2026-09-14T14:50:00Z"
        assert yesterday_group["state"] == "pending"
        assert yesterday_group["selection"] is None
        assert len(yesterday_group["segments"]) == 1
        assert yesterday_group["segments"][0]["context"] == detailed_context(
            "Before midnight"
        )
        assert future.json() == []
        assert invalid.status_code == 422
        assert invalid.json()["error"]["status"] == "INVALID_ARGUMENT"
        assert unauthorized.status_code == 401
        assert unauthorized.json()["error"]["status"] == "AUTH_INVALID_ACCESS_TOKEN"
        assert isolated.status_code == 200
        assert isolated.json() == []
    finally:
        app.dependency_overrides.pop(get_clock, None)
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id.in_(account_ids))
            )
            await session.commit()


@pytest.mark.asyncio
async def test_label_timeline_returns_open_detailed_activity_separately(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, _, _ = await create_account_with_labels(session)
        await session.commit()

    headers = auth_headers(config, account_id)
    try:
        observed_at = (
            (datetime.now(UTC))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        created = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=1,
                observed_at=observed_at,
                context=detailed_context("Current editor"),
            ),
        )
        assert created.status_code == 201

        response = await client.get(
            "/api/v1/activities/label-timeline",
            headers=headers,
        )
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["itemType"] == "in_progress_activity"
        assert response.json()[0]["endedAt"] is None
        assert response.json()[0]["context"] == detailed_context("Current editor")
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_label_timeline_reopens_confirmation_after_late_observation(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, coding_id, _ = await create_account_with_labels(session)
        await session.commit()

    headers = auth_headers(config, account_id)
    try:
        for sequence, observed_at, context in [
            (1, "2026-09-14T00:00:00Z", detailed_context("Editor")),
            (2, "2026-09-14T00:00:30Z", detailed_context("Browser")),
            (3, "2026-09-14T00:01:00Z", {"kind": "opaque"}),
        ]:
            response = await client.post(
                "/api/v1/activities",
                headers=headers,
                json=activity_request(
                    device_id=device.device_id,
                    sequence=sequence,
                    observed_at=observed_at,
                    context=context,
                ),
            )
            assert response.status_code == 201

        observed = await client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers=headers,
        )
        first_segment = next(
            segment
            for segment in observed.json()
            if segment.get("context", {}).get("app", {}).get("name", {}).get("value")
            == "Editor"
        )
        state = await client.get(
            f"/api/v1/activities/segments/{first_segment['segmentId']}/label-state",
            headers=headers,
        )
        old_version = state.json()["segmentVersion"]
        confirmation = await client.put(
            f"/api/v1/activities/segments/{first_segment['segmentId']}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": old_version,
                "selection": {"kind": "label", "labelId": str(coding_id)},
            },
        )
        assert confirmation.status_code == 200

        label_change = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        assert label_change.json()[0]["state"] == "confirmed"

        late = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=4,
                observed_at="2026-09-14T00:00:15Z",
                context=detailed_context("Editor"),
            ),
        )
        assert late.status_code == 201

        current = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        assert current.status_code == 200
        assert current.json()[0]["state"] == "pending"
        assert len(current.json()[0]["segments"]) == 2
        assert current.json()[0]["segments"][0]["segmentVersion"] != old_version
        assert current.json()[0]["segments"][0]["context"] == detailed_context("Editor")
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_projection_version_invalidates_only_changed_segments(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, coding_id, _ = await create_account_with_labels(session)
        await session.commit()
    headers = auth_headers(config, account_id)

    try:
        for sequence, observed_at, context in (
            (1, "2026-09-14T00:00:00Z", detailed_context("First")),
            (2, "2026-09-14T00:00:30Z", {"kind": "opaque"}),
        ):
            response = await client.post(
                "/api/v1/activities",
                headers=headers,
                json=activity_request(
                    device_id=device.device_id,
                    sequence=sequence,
                    observed_at=observed_at,
                    context=context,
                ),
            )
            assert response.status_code == 201
        timeline = await client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers=headers,
        )
        detailed_segment = next(
            item
            for item in timeline.json()
            if item["context"].get("kind") == "detailed"
        )
        segment_id = detailed_segment["segmentId"]
        before = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state",
            headers=headers,
        )
        old_version = before.json()["segmentVersion"]
        confirmed = await client.put(
            f"/api/v1/activities/segments/{segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": old_version,
                "selection": {"kind": "label", "labelId": str(coding_id)},
            },
        )
        assert confirmed.status_code == 200

        late = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=3,
                observed_at="2026-09-14T00:00:15Z",
                context=detailed_context("Late different context"),
            ),
        )
        assert late.status_code == 201
        changed = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state",
            headers=headers,
        )
        assert changed.status_code == 200
        assert changed.json()["state"] == "pending"
        assert changed.json()["segmentVersion"] != old_version

        stale = await client.put(
            f"/api/v1/activities/segments/{segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": old_version,
                "selection": {"kind": "unclassified"},
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["status"] == "ACTIVITY_SEGMENT_CHANGED"
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_projection_version_survives_same_result_recalculation(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, coding_id, _ = await create_account_with_labels(session)
        await session.commit()
    headers = auth_headers(config, account_id)

    try:
        for sequence, observed_at, context in (
            (1, "2026-09-14T00:00:00Z", detailed_context("Same")),
            (2, "2026-09-14T00:00:30Z", detailed_context("Same")),
            (3, "2026-09-14T00:01:00Z", {"kind": "opaque"}),
        ):
            response = await client.post(
                "/api/v1/activities",
                headers=headers,
                json=activity_request(
                    device_id=device.device_id,
                    sequence=sequence,
                    observed_at=observed_at,
                    context=context,
                ),
            )
            assert response.status_code == 201
        timeline = await client.get(
            "/api/v1/activities/timeline?date=2026-09-14",
            headers=headers,
        )
        detailed_segment = next(
            item
            for item in timeline.json()
            if item["context"].get("kind") == "detailed"
        )
        segment_id = detailed_segment["segmentId"]
        before = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state",
            headers=headers,
        )
        old_version = before.json()["segmentVersion"]
        confirmed = await client.put(
            f"/api/v1/activities/segments/{segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": old_version,
                "selection": {"kind": "label", "labelId": str(coding_id)},
            },
        )
        assert confirmed.status_code == 200

        late = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=4,
                observed_at="2026-09-14T00:00:15Z",
                context=detailed_context("Same"),
            ),
        )
        assert late.status_code == 201
        after = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state",
            headers=headers,
        )
        assert after.status_code == 200
        assert after.json()["state"] == "confirmed"
        assert after.json()["segmentVersion"] == old_version
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()
