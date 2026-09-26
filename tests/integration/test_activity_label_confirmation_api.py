import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
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
from mosemo.labels.models import (
    ActivityLabelConfirmation,
    ActivityLabelProposal,
    ActivityLabelProposalStatus,
    Label,
)
from mosemo.labels.proposals import (
    NoProposalWorkError,
    ProposalProcessor,
    ProposalScanner,
    RecentConfirmedExampleRetriever,
)
from mosemo.labels.repository import LabelRepository
from mosemo.labels.schemas import SEGMENT_VERSION_PATTERN
from mosemo.labels.suggestions import (
    ConfirmedExample,
    LabelSuggester,
    SuggestionInput,
    SuggestionResult,
)


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


async def upload_closed_detail(
    client: httpx2.AsyncClient,
    *,
    headers: dict[str, str],
    device_id: UUID,
    sequence: int,
    started_at: str,
    ended_at: str,
    name: str,
    context: Mapping[str, object] | None = None,
) -> tuple[UUID, str, str]:
    first = await client.post(
        "/api/v1/activities",
        headers=headers,
        json=activity_request(
            device_id=device_id,
            sequence=sequence,
            observed_at=started_at,
            context=context or detailed_context(name),
        ),
    )
    assert first.status_code == 201
    closed = await client.post(
        "/api/v1/activities",
        headers=headers,
        json=activity_request(
            device_id=device_id,
            sequence=sequence + 1,
            observed_at=ended_at,
            context={"kind": "opaque"},
        ),
    )
    assert closed.status_code == 201
    timeline = await client.get(
        f"/api/v1/activities/timeline?date={started_at[:10]}", headers=headers
    )
    segment_id = next(
        item["segmentId"]
        for item in timeline.json()
        if item.get("context", {}).get("app", {}).get("name", {}).get("value") == name
    )
    state = await client.get(
        f"/api/v1/activities/segments/{segment_id}/label-state", headers=headers
    )
    assert state.status_code == 200
    return UUID(first.json()["eventId"]), segment_id, state.json()["segmentVersion"]


def group_confirmation_item(
    group: dict[str, Any], selection: dict[str, str]
) -> dict[str, Any]:
    segments = group["segments"]
    assert isinstance(segments, list)
    return {
        "date": "2026-09-14",
        "groupVersion": group["groupVersion"],
        "segments": [
            {
                "segmentId": member["segmentId"],
                "segmentVersion": member["segmentVersion"],
            }
            for member in segments
        ],
        "selection": selection,
    }


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
        assert pending_body["proposal"] == {"status": "waiting"}
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
async def test_confirming_a_label_group_applies_to_every_member(
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

        timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        assert timeline.status_code == 200
        group = timeline.json()[0]
        assert group["itemType"] == "activity_group"
        assert len(group["segments"]) == 2

        confirmed = await client.post(
            "/api/v1/activities/label-confirmations",
            headers=headers,
            json={
                "items": [
                    {
                        "date": "2026-09-14",
                        "groupVersion": group["groupVersion"],
                        "segments": [
                            {
                                "segmentId": member["segmentId"],
                                "segmentVersion": member["segmentVersion"],
                            }
                            for member in group["segments"]
                        ],
                        "selection": {"kind": "label", "labelId": str(coding_id)},
                    }
                ]
            },
        )
        assert confirmed.status_code == 200
        results = confirmed.json()["items"][0]["segments"]
        assert [result["segmentId"] for result in results] == [
            member["segmentId"] for member in group["segments"]
        ]
        assert all(
            result["selection"] == {"kind": "label", "labelId": str(coding_id)}
            for result in results
        )

        updated = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()[0]["state"] == "confirmed"
        assert len(updated.json()[0]["segments"]) == 2
        assert updated.json()[1]["itemType"] == "opaque_activity"
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_batch_confirms_different_choices_and_retries_without_changing_timestamps(
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
        await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=3,
            started_at="2026-09-14T00:02:00Z",
            ended_at="2026-09-14T00:02:30Z",
            name="Browser",
        )
        timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        groups = [
            item for item in timeline.json() if item["itemType"] == "activity_group"
        ]
        assert len(groups) == 2
        request = {
            "items": [
                group_confirmation_item(
                    groups[0], {"kind": "label", "labelId": str(coding_id)}
                ),
                group_confirmation_item(groups[1], {"kind": "unclassified"}),
            ]
        }

        confirmed = await client.post(
            "/api/v1/activities/label-confirmations", headers=headers, json=request
        )
        retried = await client.post(
            "/api/v1/activities/label-confirmations", headers=headers, json=request
        )

        assert confirmed.status_code == retried.status_code == 200
        assert retried.json() == confirmed.json()
        assert confirmed.json()["items"][0]["segments"][0]["selection"] == {
            "kind": "label",
            "labelId": str(coding_id),
        }
        assert confirmed.json()["items"][1]["segments"][0]["selection"] == {
            "kind": "unclassified"
        }
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_delayed_batch_retry_cannot_undo_a_later_correction(
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
        await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        first_request = {
            "items": [
                group_confirmation_item(
                    timeline.json()[0],
                    {"kind": "label", "labelId": str(coding_id)},
                )
            ]
        }
        first = await client.post(
            "/api/v1/activities/label-confirmations",
            headers=headers,
            json=first_request,
        )
        assert first.status_code == 200

        refreshed = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        correction = await client.post(
            "/api/v1/activities/label-confirmations",
            headers=headers,
            json={
                "items": [
                    group_confirmation_item(
                        refreshed.json()[0],
                        {"kind": "label", "labelId": str(learning_id)},
                    )
                ]
            },
        )
        assert correction.status_code == 200
        assert refreshed.json()[0]["groupVersion"] != timeline.json()[0]["groupVersion"]

        delayed = await client.post(
            "/api/v1/activities/label-confirmations",
            headers=headers,
            json=first_request,
        )
        latest = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )

        assert delayed.status_code == 409
        assert delayed.json()["error"]["status"] == "ACTIVITY_SEGMENT_CHANGED"
        assert latest.json()[0]["selection"]["labelId"] == str(learning_id)
        assert latest.json()[0]["groupVersion"] != refreshed.json()[0]["groupVersion"]
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_batch_with_stale_member_rolls_back_every_group_and_identifies_failure(
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
        await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=3,
            started_at="2026-09-14T00:02:00Z",
            ended_at="2026-09-14T00:02:30Z",
            name="Browser",
        )
        timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        groups = [
            item for item in timeline.json() if item["itemType"] == "activity_group"
        ]
        request = {
            "items": [
                group_confirmation_item(
                    group, {"kind": "label", "labelId": str(coding_id)}
                )
                for group in groups
            ]
        }
        late = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=5,
                observed_at="2026-09-14T00:02:15Z",
                context=detailed_context("Late change"),
            ),
        )
        assert late.status_code == 201

        rejected = await client.post(
            "/api/v1/activities/label-confirmations", headers=headers, json=request
        )
        current = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )

        assert rejected.status_code == 409
        assert rejected.json()["error"]["status"] == "ACTIVITY_SEGMENT_CHANGED"
        assert rejected.json()["error"]["details"][0]["loc"] == [
            "body",
            "items",
            1,
            "segments",
            0,
        ]
        assert all(
            item["state"] == "pending"
            for item in current.json()
            if item["itemType"] == "activity_group"
        )
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_batch_rejects_a_partial_group_without_confirming_its_members(
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
        timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        group = timeline.json()[0]
        assert len(group["segments"]) == 2
        item = group_confirmation_item(
            group, {"kind": "label", "labelId": str(coding_id)}
        )
        item["segments"] = item["segments"][:1]

        rejected = await client.post(
            "/api/v1/activities/label-confirmations",
            headers=headers,
            json={"items": [item]},
        )
        current = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )

        assert rejected.status_code == 409
        assert rejected.json()["error"]["status"] == "ACTIVITY_SEGMENT_CHANGED"
        assert rejected.json()["error"]["details"][0]["loc"] == [
            "body",
            "items",
            0,
            "segments",
        ]
        assert current.json()[0]["state"] == "pending"
        assert len(current.json()[0]["segments"]) == 2
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_batch_rejects_duplicate_member_targets_before_any_write(
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
        await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        group = timeline.json()[0]

        rejected = await client.post(
            "/api/v1/activities/label-confirmations",
            headers=headers,
            json={
                "items": [
                    group_confirmation_item(
                        group, {"kind": "label", "labelId": str(coding_id)}
                    ),
                    group_confirmation_item(group, {"kind": "unclassified"}),
                ]
            },
        )
        current = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )

        assert rejected.status_code == 422
        assert rejected.json()["error"]["status"] == "INVALID_ARGUMENT"
        assert current.json()[0]["state"] == "pending"
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_batch_and_late_observation_never_attach_label_to_changed_activity(
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
        await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        item = group_confirmation_item(
            timeline.json()[0], {"kind": "label", "labelId": str(coding_id)}
        )

        confirmed, late = await asyncio.gather(
            client.post(
                "/api/v1/activities/label-confirmations",
                headers=headers,
                json={"items": [item]},
            ),
            client.post(
                "/api/v1/activities",
                headers=headers,
                json=activity_request(
                    device_id=device.device_id,
                    sequence=3,
                    observed_at="2026-09-14T00:00:15Z",
                    context=detailed_context("Late change"),
                ),
            ),
        )
        current = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )

        assert late.status_code == 201
        assert confirmed.status_code in {200, 409}
        assert current.status_code == 200
        assert all(
            group["state"] == "pending"
            for group in current.json()
            if group["itemType"] == "activity_group"
        )
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_batch_rejects_foreign_segment_without_confirming_own_group(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, coding_id, _ = await create_account_with_labels(session)
        other_id, other_device, _, _ = await create_account_with_labels(session)
        await session.commit()

    headers = auth_headers(config, account_id)
    other_headers = auth_headers(config, other_id)
    try:
        await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Owner",
        )
        await upload_closed_detail(
            client,
            headers=other_headers,
            device_id=other_device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Other",
        )
        owner_timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        other_timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=other_headers,
        )
        owner_group = owner_timeline.json()[0]
        own_item = group_confirmation_item(
            owner_group, {"kind": "label", "labelId": str(coding_id)}
        )
        foreign_item = group_confirmation_item(
            other_timeline.json()[0], {"kind": "unclassified"}
        )

        foreign = await client.post(
            "/api/v1/activities/label-confirmations",
            headers=headers,
            json={"items": [own_item, foreign_item]},
        )
        assert foreign.status_code == 404
        assert foreign.json()["error"]["status"] == "ACTIVITY_SEGMENT_NOT_FOUND"
        assert foreign.json()["error"]["details"][0]["loc"] == [
            "body",
            "items",
            1,
            "segments",
            0,
        ]

        current = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        assert current.json()[0]["state"] == "pending"
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id.in_((account_id, other_id)))
            )
            await session.commit()


@pytest.mark.asyncio
async def test_batch_rejects_archived_label(
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
        await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        item = group_confirmation_item(
            timeline.json()[0], {"kind": "label", "labelId": str(coding_id)}
        )
        async with session_factory() as session:
            archived = await session.get(Label, coding_id)
            assert archived is not None
            archived.archived_at = datetime.now(UTC)
            await session.commit()

        unavailable = await client.post(
            "/api/v1/activities/label-confirmations",
            headers=headers,
            json={"items": [item]},
        )

        assert unavailable.status_code == 404
        assert unavailable.json()["error"]["status"] == "LABEL_NOT_AVAILABLE"
        assert unavailable.json()["error"]["details"][0]["loc"] == ["body", "items", 0]
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_batch_rejects_opaque_segment(
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
        await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        group, opaque = timeline.json()
        not_labelable = await client.post(
            "/api/v1/activities/label-confirmations",
            headers=headers,
            json={
                "items": [
                    {
                        "date": "2026-09-14",
                        "groupVersion": group["groupVersion"],
                        "segments": [
                            {
                                "segmentId": opaque["segmentId"],
                                "segmentVersion": "0" * 64,
                            }
                        ],
                        "selection": {"kind": "unclassified"},
                    }
                ]
            },
        )

        assert not_labelable.status_code == 409
        assert not_labelable.json()["error"]["status"] == (
            "ACTIVITY_SEGMENT_NOT_LABELABLE"
        )
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_batch_requires_authentication(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
) -> None:
    client, _, _ = label_api_client
    unauthenticated = await client.post(
        "/api/v1/activities/label-confirmations",
        json={"items": []},
    )
    assert unauthenticated.status_code == 401


@pytest.mark.asyncio
async def test_batch_uses_user_choices_with_ready_and_waiting_proposals(
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
        first_event_id, _, _ = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=3,
            started_at="2026-09-14T00:02:00Z",
            ended_at="2026-09-14T00:02:30Z",
            name="Browser",
        )
        processor = ProposalProcessor(
            session_factory, suggester=FixedSuggester(coding_id)
        )
        assert await processor.process_candidate(account_id, first_event_id)
        timeline = await client.get(
            "/api/v1/activities/label-timeline?date=2026-09-14",
            headers=headers,
        )
        groups = [
            item for item in timeline.json() if item["itemType"] == "activity_group"
        ]

        confirmed = await client.post(
            "/api/v1/activities/label-confirmations",
            headers=headers,
            json={
                "items": [
                    group_confirmation_item(
                        groups[0],
                        {"kind": "label", "labelId": str(learning_id)},
                    ),
                    group_confirmation_item(groups[1], {"kind": "unclassified"}),
                ]
            },
        )

        assert confirmed.status_code == 200
        first = confirmed.json()["items"][0]["segments"][0]
        second = confirmed.json()["items"][1]["segments"][0]
        assert first["selection"]["labelId"] == str(learning_id)
        assert first["proposal"]["selection"]["labelId"] == str(coding_id)
        assert second["selection"] == {"kind": "unclassified"}
        assert second["proposal"] is None
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


class FixedSuggester:
    def __init__(self, label_id: UUID) -> None:
        self.label_id = label_id
        self.inputs: list[SuggestionInput] = []

    async def suggest(self, request: SuggestionInput) -> SuggestionResult:
        self.inputs.append(request)
        return SuggestionResult(
            label_id=self.label_id,
            provider="test",
            model="fixed",
            prompt_version="v1",
            input_tokens=10,
            output_tokens=2,
        )


class RecoveringSuggester:
    def __init__(self) -> None:
        self.calls = 0

    async def suggest(self, request: SuggestionInput) -> SuggestionResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary provider failure")
        return SuggestionResult(
            label_id=None,
            provider="test",
            model="fixed",
            prompt_version="v1",
            input_tokens=None,
            output_tokens=None,
        )


class FailingSuggester:
    def __init__(self) -> None:
        self.calls = 0

    async def suggest(self, request: SuggestionInput) -> SuggestionResult:
        self.calls += 1
        raise RuntimeError("provider unavailable")


class CallbackSuggester(FixedSuggester):
    def __init__(self, label_id: UUID, callback: Callable[[], Awaitable[None]]) -> None:
        super().__init__(label_id)
        self.callback = callback

    async def suggest(self, request: SuggestionInput) -> SuggestionResult:
        result = await super().suggest(request)
        await self.callback()
        return result


class BlockingSuggester(FixedSuggester):
    def __init__(self, label_id: UUID) -> None:
        super().__init__(label_id)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def suggest(self, request: SuggestionInput) -> SuggestionResult:
        self.started.set()
        await self.release.wait()
        return await super().suggest(request)


class CountingProposalProcessor(ProposalProcessor):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        suggester: LabelSuggester,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(session_factory, suggester=suggester, now=now)
        self.candidates: list[tuple[UUID, UUID]] = []

    async def process_candidate(self, account_id: UUID, first_event_id: UUID) -> bool:
        self.candidates.append((account_id, first_event_id))
        return await super().process_candidate(account_id, first_event_id)


@pytest.mark.asyncio
async def test_worker_proposal_is_visible_but_not_confirmed_over_http(
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
        browser_context = detailed_context("Editor")
        browser_context["web"] = {
            "kind": "browser",
            "tabTitle": {"status": "captured", "value": "Keyboard comparison"},
            "url": {
                "status": "captured",
                "value": "https://example.com/private?token=secret#fragment",
            },
        }
        event_id, segment_id, version = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
            context=browser_context,
        )

        suggester = FixedSuggester(coding_id)
        processor = ProposalProcessor(session_factory, suggester=suggester)
        await processor.process_candidate(account_id, event_id)
        assert "web_host: example.com" in suggester.inputs[0].activity
        assert "private" not in suggester.inputs[0].activity
        assert "secret" not in suggester.inputs[0].activity

        state = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state", headers=headers
        )
        assert state.status_code == 200
        assert state.json()["state"] == "pending"
        assert state.json()["proposal"]["status"] == "ready"
        assert state.json()["proposal"]["selection"] == {
            "kind": "label",
            "labelId": str(coding_id),
        }

        corrected = await client.put(
            f"/api/v1/activities/segments/{segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": version,
                "selection": {"kind": "label", "labelId": str(learning_id)},
            },
        )
        assert corrected.status_code == 200
        assert corrected.json()["selection"]["labelId"] == str(learning_id)
        assert corrected.json()["proposal"]["selection"]["labelId"] == str(coding_id)
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_failed_proposal_stays_pending_then_retries_as_unclassified(
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
        event_id, segment_id, _ = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        current_time = [datetime(2026, 9, 14, 1, tzinfo=UTC)]
        suggester = RecoveringSuggester()
        processor = ProposalProcessor(
            session_factory, suggester=suggester, now=lambda: current_time[0]
        )
        assert await processor.process_candidate(account_id, event_id)
        failed = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state", headers=headers
        )
        assert failed.json()["state"] == "pending"
        assert failed.json()["proposal"] == {"status": "failed"}
        assert not await processor.process_candidate(account_id, event_id)
        assert suggester.calls == 1

        current_time[0] += timedelta(minutes=1, seconds=1)
        assert await processor.process_candidate(account_id, event_id)
        ready = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state", headers=headers
        )
        assert ready.json()["state"] == "pending"
        assert ready.json()["proposal"]["selection"] == {"kind": "unclassified"}
        assert not await processor.process_candidate(account_id, event_id)
        assert suggester.calls == 2
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_three_failed_attempts_leave_the_proposal_pending_for_review(
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
        event_id, segment_id, _ = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        current_time = [datetime(2026, 9, 14, 1, tzinfo=UTC)]
        suggester = FailingSuggester()
        processor = ProposalProcessor(
            session_factory, suggester=suggester, now=lambda: current_time[0]
        )
        for delay in (0, 1, 5):
            current_time[0] += timedelta(minutes=delay, seconds=1)
            assert await processor.process_candidate(account_id, event_id)
        current_time[0] += timedelta(days=1)
        assert not await processor.process_candidate(account_id, event_id)
        assert suggester.calls == 3
        async with session_factory() as session:
            proposal = await session.scalar(
                select(ActivityLabelProposal).where(
                    ActivityLabelProposal.account_id == account_id,
                    ActivityLabelProposal.first_event_id == event_id,
                )
            )
            assert proposal is not None
            assert proposal.status is ActivityLabelProposalStatus.FAILED
            assert (
                await session.scalar(
                    text(
                        "SELECT status FROM activity_label_proposals "
                        "WHERE proposal_id = :proposal_id"
                    ),
                    {"proposal_id": proposal.proposal_id},
                )
                == "failed"
            )
            assert proposal.attempt_count == 3
        state = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state", headers=headers
        )
        assert state.json()["state"] == "pending"
        assert state.json()["proposal"] == {"status": "failed"}
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_claim_without_work_raises_and_commits_expired_final_lease(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, label_id, _ = await create_account_with_labels(session)
        await session.commit()
    headers = auth_headers(config, account_id)
    try:
        event_id, _, _ = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        now = datetime(2026, 9, 14, 1, tzinfo=UTC)
        processor = ProposalProcessor(
            session_factory, suggester=FixedSuggester(label_id), now=lambda: now
        )
        with pytest.raises(NoProposalWorkError):
            await processor._claim(account_id, uuid4())

        assert await processor.process_candidate(account_id, event_id)
        with pytest.raises(NoProposalWorkError):
            await processor._claim(account_id, event_id)

        async with session_factory() as session, session.begin():
            proposal = await session.scalar(
                select(ActivityLabelProposal).where(
                    ActivityLabelProposal.account_id == account_id,
                    ActivityLabelProposal.first_event_id == event_id,
                )
            )
            assert proposal is not None
            proposal.status = ActivityLabelProposalStatus.PROCESSING
            proposal.attempt_count = 3
            proposal.lease_token = uuid4()
            proposal.lease_expires_at = now - timedelta(seconds=1)

        with pytest.raises(NoProposalWorkError):
            await processor._claim(account_id, event_id)

        async with session_factory() as session:
            proposal = await session.scalar(
                select(ActivityLabelProposal).where(
                    ActivityLabelProposal.account_id == account_id,
                    ActivityLabelProposal.first_event_id == event_id,
                )
            )
            assert proposal is not None
            assert proposal.status is ActivityLabelProposalStatus.FAILED
            assert proposal.lease_token is None
            assert proposal.lease_expires_at is None
            assert proposal.attempt_count == 3
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_scanner_discovers_explicit_and_silence_closed_details_only(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, label_id, _ = await create_account_with_labels(session)
        open_account_id, open_device, _, _ = await create_account_with_labels(session)
        await session.commit()
    headers = auth_headers(config, account_id)
    open_headers = auth_headers(config, open_account_id)
    now = datetime.now(UTC)
    try:
        for sequence, observed_at, context in (
            (1, "2026-09-14T00:00:00Z", detailed_context("Explicit")),
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
        gap = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=state_change_request(
                device_id=device.device_id,
                sequence=3,
                observed_at="2026-09-14T00:01:00Z",
            ),
        )
        assert gap.status_code == 201
        silence_record = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=4,
                observed_at="2026-09-14T00:02:00Z",
                context=detailed_context("Silence"),
            ),
        )
        assert silence_record.status_code == 201
        open_record = await client.post(
            "/api/v1/activities",
            headers=open_headers,
            json=activity_request(
                device_id=open_device.device_id,
                sequence=1,
                observed_at=now.isoformat().replace("+00:00", "Z"),
                context=detailed_context("Open"),
            ),
        )
        assert open_record.status_code == 201

        suggester = FixedSuggester(label_id)
        processor = ProposalProcessor(
            session_factory, suggester=suggester, now=lambda: now
        )
        scanner = ProposalScanner(session_factory, processor, now=lambda: now)
        assert await scanner.run_once() == 2
        assert len(suggester.inputs) == 2
        assert await scanner.run_once() == 0

        timeline = await client.get(
            "/api/v1/activities/timeline?date=2026-09-14", headers=headers
        )
        for segment in timeline.json():
            if segment.get("context", {}).get("kind") == "detailed":
                state = await client.get(
                    f"/api/v1/activities/segments/{segment['segmentId']}/label-state",
                    headers=headers,
                )
                assert state.json()["proposal"]["status"] == "ready"
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(
                    Account.account_id.in_((account_id, open_account_id))
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_scanner_bounds_batches_and_skips_currently_completed_versions(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, label_id, _ = await create_account_with_labels(session)
        await session.commit()
    headers = auth_headers(config, account_id)
    now = datetime.now(UTC)
    records: list[tuple[UUID, str, str]] = []
    try:
        for index in range(6):
            minute = index
            event_id, segment_id, version = await upload_closed_detail(
                client,
                headers=headers,
                device_id=device.device_id,
                sequence=index * 2 + 1,
                started_at=f"2026-09-14T00:{minute:02d}:00Z",
                ended_at=f"2026-09-14T00:{minute:02d}:30Z",
                name=f"Editor {index}",
            )
            records.append((event_id, segment_id, version))

        completed_suggester = FixedSuggester(label_id)
        completed_processor = ProposalProcessor(
            session_factory, suggester=completed_suggester, now=lambda: now
        )
        for event_id, _, _ in records[:4]:
            assert await completed_processor.process_candidate(account_id, event_id)

        _, segment_id, version = records[4]
        confirmation = await client.put(
            f"/api/v1/activities/segments/{segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": version,
                "selection": {"kind": "label", "labelId": str(label_id)},
            },
        )
        assert confirmation.status_code == 200
        late_observation = await client.post(
            "/api/v1/activities",
            headers=headers,
            json=activity_request(
                device_id=device.device_id,
                sequence=13,
                observed_at="2026-09-14T00:00:15Z",
                context=detailed_context("Rebuilt Editor"),
            ),
        )
        assert late_observation.status_code == 201

        scanner_suggester = FixedSuggester(label_id)
        processor = CountingProposalProcessor(
            session_factory, suggester=scanner_suggester, now=lambda: now
        )
        scanner = ProposalScanner(session_factory, processor, now=lambda: now)
        total_scanned = 0
        processed = 0
        while True:
            batch = await scanner.run_batch(max_scanned=2)
            assert batch.scanned <= 2
            total_scanned += batch.scanned
            processed += batch.processed
            if batch.sweep_complete:
                break

        assert total_scanned >= 6
        assert processed >= 2
        assert len(processor.candidates) == processed
        activities = {request.activity for request in scanner_suggester.inputs}
        assert any(activity.endswith("Editor 5") for activity in activities)
        async with session_factory() as session:
            rebuilt_proposals = list(
                (
                    await session.scalars(
                        select(ActivityLabelProposal).where(
                            ActivityLabelProposal.account_id == account_id,
                            ActivityLabelProposal.first_event_id == records[0][0],
                        )
                    )
                ).all()
            )
        assert len(rebuilt_proposals) == 2
        assert all(
            proposal.status is ActivityLabelProposalStatus.READY
            for proposal in rebuilt_proposals
        )

        prior_calls = len(scanner_suggester.inputs)
        next_batch = await scanner.run_batch(max_scanned=2)
        assert next_batch.processed == 0
        assert len(scanner_suggester.inputs) == prior_calls
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_next_proposal_uses_only_owners_latest_confirmation_and_stays_stable(
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
        other_id, other_device, other_label_id, _ = await create_account_with_labels(
            session
        )
        await session.commit()
    headers = auth_headers(config, account_id)
    other_headers = auth_headers(config, other_id)
    try:
        _, first_segment_id, first_version = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="First",
        )
        first_confirm = await client.put(
            f"/api/v1/activities/segments/{first_segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": first_version,
                "selection": {"kind": "label", "labelId": str(coding_id)},
            },
        )
        assert first_confirm.status_code == 200
        corrected = await client.put(
            f"/api/v1/activities/segments/{first_segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": first_version,
                "selection": {"kind": "label", "labelId": str(learning_id)},
            },
        )
        assert corrected.status_code == 200

        _, other_segment_id, other_version = await upload_closed_detail(
            client,
            headers=other_headers,
            device_id=other_device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Other",
        )
        other_confirm = await client.put(
            f"/api/v1/activities/segments/{other_segment_id}/label-confirmation",
            headers=other_headers,
            json={
                "segmentVersion": other_version,
                "selection": {"kind": "label", "labelId": str(other_label_id)},
            },
        )
        assert other_confirm.status_code == 200

        event_id, second_segment_id, _ = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=3,
            started_at="2026-09-14T00:02:00Z",
            ended_at="2026-09-14T00:02:30Z",
            name="Second",
        )
        suggester = FixedSuggester(coding_id)
        processor = ProposalProcessor(session_factory, suggester=suggester)
        assert await processor.process_candidate(account_id, event_id)
        assert len(suggester.inputs) == 1
        assert [example.label_id for example in suggester.inputs[0].examples] == [
            learning_id
        ]

        changed_history = await client.put(
            f"/api/v1/activities/segments/{first_segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": first_version,
                "selection": {"kind": "label", "labelId": str(coding_id)},
            },
        )
        assert changed_history.status_code == 200
        assert not await processor.process_candidate(account_id, event_id)
        assert len(suggester.inputs) == 1
        state = await client.get(
            f"/api/v1/activities/segments/{second_segment_id}/label-state",
            headers=headers,
        )
        assert state.json()["proposal"]["selection"]["labelId"] == str(coding_id)
        assert state.json()["state"] == "pending"
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id.in_((account_id, other_id)))
            )
            await session.commit()


@pytest.mark.asyncio
async def test_proposal_examples_skip_stale_and_archived_confirmations(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        (
            account_id,
            device,
            archived_label_id,
            active_label_id,
        ) = await create_account_with_labels(session)
        await session.commit()
    headers = auth_headers(config, account_id)
    try:
        target = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Target",
        )
        examples = {
            "active": await upload_closed_detail(
                client,
                headers=headers,
                device_id=device.device_id,
                sequence=3,
                started_at="2026-09-14T00:02:00Z",
                ended_at="2026-09-14T00:02:30Z",
                name="Active example",
            ),
            "unclassified": await upload_closed_detail(
                client,
                headers=headers,
                device_id=device.device_id,
                sequence=5,
                started_at="2026-09-14T00:04:00Z",
                ended_at="2026-09-14T00:04:30Z",
                name="Unclassified example",
            ),
            "archived": await upload_closed_detail(
                client,
                headers=headers,
                device_id=device.device_id,
                sequence=7,
                started_at="2026-09-14T00:06:00Z",
                ended_at="2026-09-14T00:06:30Z",
                name="Archived example",
            ),
            "stale": await upload_closed_detail(
                client,
                headers=headers,
                device_id=device.device_id,
                sequence=9,
                started_at="2026-09-14T00:08:00Z",
                ended_at="2026-09-14T00:08:30Z",
                name="Stale example",
            ),
        }
        selections = {
            "active": {"kind": "label", "labelId": str(active_label_id)},
            "unclassified": {"kind": "unclassified"},
            "archived": {"kind": "label", "labelId": str(archived_label_id)},
            "stale": {"kind": "label", "labelId": str(active_label_id)},
        }
        for name, (_, segment_id, version) in examples.items():
            response = await client.put(
                f"/api/v1/activities/segments/{segment_id}/label-confirmation",
                headers=headers,
                json={"segmentVersion": version, "selection": selections[name]},
            )
            assert response.status_code == 200

        async with session_factory() as session, session.begin():
            confirmations = (
                await session.scalars(
                    select(ActivityLabelConfirmation).where(
                        ActivityLabelConfirmation.account_id == account_id
                    )
                )
            ).all()
            by_event_id = {row.first_event_id: row for row in confirmations}
            offsets = {
                "active": 1,
                "unclassified": 2,
                "archived": 4,
                "stale": 3,
            }
            base_time = datetime(2026, 9, 14, tzinfo=UTC)
            for name, (event_id, _, _) in examples.items():
                by_event_id[event_id].updated_at = base_time + timedelta(
                    minutes=offsets[name]
                )
            by_event_id[examples["stale"][0]].segment_version = "0" * 64
            label = await session.get(Label, archived_label_id)
            assert label is not None
            label.archived_at = datetime.now(UTC)

        monkeypatch.setattr(RecentConfirmedExampleRetriever, "PAGE_SIZE", 2)
        suggester = FixedSuggester(active_label_id)
        processor = ProposalProcessor(session_factory, suggester=suggester)
        assert await processor.process_candidate(account_id, target[0])

        actual = suggester.inputs[0].examples
        assert [example.confirmation_id for example in actual] == [
            by_event_id[examples["unclassified"][0]].confirmation_id,
            by_event_id[examples["active"][0]].confirmation_id,
        ]
        assert [example.label_id for example in actual] == [None, active_label_id]
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_archived_suggestion_is_retained_and_blocks_label_deletion(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, label_id, _ = await create_account_with_labels(session)
        await session.commit()
    headers = auth_headers(config, account_id)
    try:
        event_id, segment_id, version = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        processor = ProposalProcessor(
            session_factory, suggester=FixedSuggester(label_id)
        )
        assert await processor.process_candidate(account_id, event_id)
        async with session_factory() as session, session.begin():
            label = await session.get(Label, label_id)
            assert label is not None
            label.archived_at = datetime.now(UTC)
        async with session_factory() as session:
            with pytest.raises(IntegrityError):
                async with session.begin():
                    await session.execute(
                        delete(Label).where(Label.label_id == label_id)
                    )

        state = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state", headers=headers
        )
        assert state.json()["proposal"]["selection"] == {
            "kind": "label",
            "labelId": str(label_id),
        }
        unavailable = await client.put(
            f"/api/v1/activities/segments/{segment_id}/label-confirmation",
            headers=headers,
            json={
                "segmentVersion": version,
                "selection": {"kind": "label", "labelId": str(label_id)},
            },
        )
        assert unavailable.status_code == 404
        assert unavailable.json()["error"]["status"] == "LABEL_NOT_AVAILABLE"
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_confirmation_during_model_call_does_not_publish_a_proposal(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, label_id, _ = await create_account_with_labels(session)
        await session.commit()
    headers = auth_headers(config, account_id)
    try:
        event_id, segment_id, version = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )

        async def confirm_while_running() -> None:
            response = await client.put(
                f"/api/v1/activities/segments/{segment_id}/label-confirmation",
                headers=headers,
                json={
                    "segmentVersion": version,
                    "selection": {"kind": "label", "labelId": str(label_id)},
                },
            )
            assert response.status_code == 200

        processor = ProposalProcessor(
            session_factory,
            suggester=CallbackSuggester(label_id, confirm_while_running),
        )
        assert await processor.process_candidate(account_id, event_id)
        state = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state", headers=headers
        )
        assert state.json()["state"] == "confirmed"
        assert state.json()["selection"]["labelId"] == str(label_id)
        assert state.json()["proposal"] is None
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_confirmation_and_activity_collection_continue_during_example_lookup(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, label_id, _ = await create_account_with_labels(session)
        await session.commit()
    headers = auth_headers(config, account_id)
    try:
        event_id, segment_id, version = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )

        lookup_started = asyncio.Event()
        release_lookup = asyncio.Event()
        original_find = RecentConfirmedExampleRetriever.find

        async def pause_example_lookup(
            retriever: RecentConfirmedExampleRetriever,
            *,
            account_id: UUID,
            exclude_first_event_id: UUID,
            active_label_ids: set[UUID],
            now: datetime,
        ) -> list[ConfirmedExample]:
            lookup_started.set()
            await release_lookup.wait()
            return await original_find(
                retriever,
                account_id=account_id,
                exclude_first_event_id=exclude_first_event_id,
                active_label_ids=active_label_ids,
                now=now,
            )

        monkeypatch.setattr(
            RecentConfirmedExampleRetriever, "find", pause_example_lookup
        )
        processor = ProposalProcessor(
            session_factory, suggester=FixedSuggester(label_id)
        )
        task = asyncio.create_task(processor.process_candidate(account_id, event_id))
        await asyncio.wait_for(lookup_started.wait(), timeout=3)
        try:
            confirmation = await asyncio.wait_for(
                client.put(
                    f"/api/v1/activities/segments/{segment_id}/label-confirmation",
                    headers=headers,
                    json={
                        "segmentVersion": version,
                        "selection": {"kind": "label", "labelId": str(label_id)},
                    },
                ),
                timeout=2.5,
            )
            assert confirmation.status_code == 200
            collected = await asyncio.wait_for(
                client.post(
                    "/api/v1/activities",
                    headers=headers,
                    json=activity_request(
                        device_id=device.device_id,
                        sequence=3,
                        observed_at="2026-09-14T00:00:45Z",
                        context=detailed_context("Editor"),
                    ),
                ),
                timeout=2.5,
            )
            assert collected.status_code == 201
        finally:
            release_lookup.set()
            did_process = await task
        assert did_process

        async with session_factory() as session:
            proposal = await session.scalar(
                select(ActivityLabelProposal).where(
                    ActivityLabelProposal.account_id == account_id,
                    ActivityLabelProposal.first_event_id == event_id,
                )
            )
            assert proposal is not None
            assert proposal.status is ActivityLabelProposalStatus.SUPERSEDED
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_projection_change_during_model_call_discards_stale_result(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        account_id, device, label_id, _ = await create_account_with_labels(session)
        await session.commit()
    headers = auth_headers(config, account_id)
    try:
        event_id, segment_id, version = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )

        async def insert_late_observation() -> None:
            late = await client.post(
                "/api/v1/activities",
                headers=headers,
                json=activity_request(
                    device_id=device.device_id,
                    sequence=3,
                    observed_at="2026-09-14T00:00:15Z",
                    context=detailed_context("Different"),
                ),
            )
            assert late.status_code == 201

        processor = ProposalProcessor(
            session_factory,
            suggester=CallbackSuggester(label_id, insert_late_observation),
        )
        assert await processor.process_candidate(account_id, event_id)
        changed = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state", headers=headers
        )
        assert changed.status_code == 200
        assert changed.json()["state"] == "pending"
        assert changed.json()["segmentVersion"] != version
        assert changed.json()["proposal"] == {"status": "waiting"}

        next_processor = ProposalProcessor(
            session_factory, suggester=FixedSuggester(label_id)
        )
        assert await next_processor.process_candidate(account_id, event_id)
        current = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state", headers=headers
        )
        assert current.json()["proposal"]["status"] == "ready"
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id == account_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_expired_worker_lease_cannot_overwrite_newer_proposal(
    label_api_client: tuple[
        httpx2.AsyncClient, async_sessionmaker[AsyncSession], FastAPI
    ],
    config: Config,
) -> None:
    client, session_factory, _ = label_api_client
    async with session_factory() as session:
        (
            account_id,
            device,
            first_label_id,
            second_label_id,
        ) = await create_account_with_labels(session)
        await session.commit()
    headers = auth_headers(config, account_id)
    try:
        event_id, segment_id, _ = await upload_closed_detail(
            client,
            headers=headers,
            device_id=device.device_id,
            sequence=1,
            started_at="2026-09-14T00:00:00Z",
            ended_at="2026-09-14T00:00:30Z",
            name="Editor",
        )
        current_time = [datetime.now(UTC)]
        blocked = BlockingSuggester(first_label_id)
        first_processor = ProposalProcessor(
            session_factory, suggester=blocked, now=lambda: current_time[0]
        )
        first_task = asyncio.create_task(
            first_processor.process_candidate(account_id, event_id)
        )
        await asyncio.wait_for(blocked.started.wait(), timeout=3)
        processing = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state", headers=headers
        )
        assert processing.json()["proposal"] == {"status": "processing"}

        duplicate = FixedSuggester(second_label_id)
        second_processor = ProposalProcessor(
            session_factory,
            suggester=duplicate,
            now=lambda: current_time[0],
        )
        assert not await second_processor.process_candidate(account_id, event_id)
        assert duplicate.inputs == []

        current_time[0] += timedelta(minutes=2, seconds=1)
        assert await second_processor.process_candidate(account_id, event_id)
        blocked.release.set()
        assert await first_task
        ready = await client.get(
            f"/api/v1/activities/segments/{segment_id}/label-state", headers=headers
        )
        assert ready.json()["proposal"]["selection"]["labelId"] == str(second_label_id)
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
            "/api/v1/activities/timeline?date="
            f"{now.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()}",
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
        group_version = response.json()[0]["groupVersion"]
        assert re.fullmatch(SEGMENT_VERSION_PATTERN, group_version)
        assert response.json() == [
            {
                "itemType": "activity_group",
                "groupVersion": group_version,
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
