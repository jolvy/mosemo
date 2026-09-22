import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.api import v1_api_router
from mosemo.auth.tokens import TokenService
from mosemo.config import Config, get_config
from mosemo.database import get_session
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
) -> AsyncIterator[tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]]]:
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
        yield client, session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_label_state_can_be_confirmed_and_corrected_over_http(
    label_api_client: tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]],
    config: Config,
) -> None:
    client, session_factory = label_api_client
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
    label_api_client: tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]],
    config: Config,
) -> None:
    client, session_factory = label_api_client
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
async def test_projection_version_invalidates_only_changed_segments(
    label_api_client: tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]],
    config: Config,
) -> None:
    client, session_factory = label_api_client
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
    label_api_client: tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession]],
    config: Config,
) -> None:
    client, session_factory = label_api_client
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
