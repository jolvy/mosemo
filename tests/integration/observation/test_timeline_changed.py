"""`TimelineChanged` from every path that replaces timeline segments."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.bootstrap import create_device_directory
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.devices import Device
from mosemo.observation.application.activities import RecordActivity
from mosemo.observation.bootstrap import create_process_activity_batch
from mosemo.observation.domain.timeline import project_events
from mosemo.observation.infrastructure.activity_records import (
    ActivityRecordRepository,
)
from mosemo.observation.infrastructure.timeline_segments import (
    TimelineSegmentRepository,
)
from mosemo.observation.presentation.activities.mappers import (
    ActivityRecordMapper,
    activity_batch,
)
from mosemo.observation.presentation.activities.schemas import ActivityBatchRequest
from mosemo.observation.public import TimelineChanged
from mosemo.shared.application.events import EventDispatcher
from tests.support.clock import FakeClock
from tests.support.observation import batch, record

T = datetime(2026, 9, 14, tzinfo=UTC)
OPAQUE: dict[str, object] = {"kind": "opaque"}


def detailed(name: str) -> dict[str, object]:
    return {
        "kind": "detailed",
        "app": {
            "bundleId": {"status": "absent"},
            "name": {"status": "captured", "value": name},
        },
        "window": {"status": "absent"},
        "web": {"kind": "not_applicable"},
    }


def collection_active(sequence: int, seconds: int) -> dict[str, object]:
    observed = T + timedelta(seconds=seconds)
    return {
        "eventId": str(uuid4()),
        "sequence": sequence,
        "recordType": "collection_state_changed",
        "observedAt": observed.isoformat().replace("+00:00", "Z"),
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "state": "active",
        "reason": "user_resumed",
    }


def changes(account_id: UUID, *spans: tuple[int, int]) -> list[TimelineChanged]:
    return [
        TimelineChanged(
            account_id=account_id,
            changed_from=T + timedelta(seconds=start),
            changed_to=T + timedelta(seconds=end),
        )
        for start, end in spans
    ]


def collecting_dispatcher() -> tuple[EventDispatcher, list[TimelineChanged]]:
    dispatcher = EventDispatcher()
    seen: list[TimelineChanged] = []

    async def collect(event: TimelineChanged, _: AsyncSession) -> None:
        seen.append(event)

    dispatcher.subscribe(TimelineChanged, collect)
    return dispatcher, seen


async def account_with_devices(
    session: AsyncSession, devices: int = 1
) -> tuple[UUID, list[UUID]]:
    account = Account(
        provider=AccountProvider.KAKAO, provider_subject=f"changed-{uuid4()}"
    )
    session.add(account)
    await session.flush()
    created = [
        Device(account_id=account.id, idempotency_key=uuid4()) for _ in range(devices)
    ]
    session.add_all(created)
    await session.flush()
    await session.commit()
    return account.id, [device.id for device in created]


def batch_of(device_id: UUID, records: list[dict[str, object]]):
    return activity_batch(
        ActivityBatchRequest.model_validate(batch(device_id, records))
    )


@pytest.mark.asyncio
async def test_record_activity_records_timeline_change_only_when_segments_change(
    integration_session: AsyncSession,
) -> None:
    account_id, (device_id,) = await account_with_devices(integration_session)
    dispatcher, seen = collecting_dispatcher()
    use_case = RecordActivity(
        devices=create_device_directory(),
        dispatcher=dispatcher,
        clock=FakeClock(T + timedelta(hours=1)),
    )
    mapper = ActivityRecordMapper()

    async def send(source: dict[str, object]) -> None:
        request = ActivityBatchRequest.model_validate(batch(device_id, [source]))
        (entity,) = [mapper.to_entity(r) for r in request.activity_records()]
        await use_case.execute(
            integration_session, account_id=account_id, record=entity
        )

    first = record(1, 0, context=OPAQUE)
    await send(first)
    assert seen == changes(account_id, (0, 0))
    await send(record(2, 10, context=OPAQUE))
    assert seen[1:] == changes(account_id, (0, 10))
    await send(record(3, 20, context=detailed("B")))
    assert seen[2:] == changes(account_id, (0, 20))

    # An active state while observing leaves the segments as they are.
    await send(collection_active(4, 25))
    # The same observation again is the stored record; nothing is projected.
    await send(first)
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_batch_append_and_replay_record_timeline_change(
    integration_session: AsyncSession,
) -> None:
    account_id, (device_id, other_device) = await account_with_devices(
        integration_session, devices=2
    )
    dispatcher, seen = collecting_dispatcher()
    use_case = create_process_activity_batch(
        devices=create_device_directory(),
        dispatcher=dispatcher,
        clock=FakeClock(T + timedelta(hours=1)),
    )

    async def process(device: UUID, records: list[dict[str, object]]) -> None:
        await use_case.execute(
            integration_session, account_id=account_id, batch=batch_of(device, records)
        )

    first = [record(0, 0, context=OPAQUE), record(1, 10, context=OPAQUE)]
    await process(device_id, first)
    assert seen == changes(account_id, (0, 10))
    # Append after the last segment.
    await process(
        device_id,
        [record(2, 20, context=detailed("B")), record(3, 30, context=detailed("B"))],
    )
    assert seen[1:] == changes(account_id, (0, 30))
    # Replay from a past observation of another device.
    # B after it is untouched, so the change ends where the new C segment ends.
    await process(other_device, [record(0, 15, context=detailed("C"))])
    assert seen[2:] == changes(account_id, (0, 20))

    # The same observations in a new batch are duplicates; nothing is projected.
    await process(device_id, first)
    # An active state after the last observation leaves the segments unchanged.
    await process(device_id, [collection_active(4, 40)])
    assert len(seen) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("segments_stored", [True, False])
async def test_first_receipt_rebuild_records_change_only_for_new_segments(
    integration_session: AsyncSession, segments_stored: bool
) -> None:
    account_id, (device_id,) = await account_with_devices(integration_session)
    clock = FakeClock(T + timedelta(hours=1))
    legacy = [record(0, 0, context=OPAQUE), record(1, 10, context=detailed("B"))]
    request = ActivityBatchRequest.model_validate(batch(device_id, legacy))
    records = ActivityRecordRepository()
    stored = [
        (
            await records.get_or_create(
                integration_session, ActivityRecordMapper().to_entity(r)
            )
        )[0]
        for r in request.activity_records()
    ]
    if segments_stored:
        await TimelineSegmentRepository().replace_account_segments(
            integration_session,
            account_id=account_id,
            projected=project_events(stored, now=clock.now),
            existing=[],
            event_observed_at=stored[-1].observed_at,
        )
    await integration_session.commit()
    dispatcher, seen = collecting_dispatcher()

    # The duplicate-only first receipt still rebuilds the whole legacy timeline.
    await create_process_activity_batch(
        devices=create_device_directory(), dispatcher=dispatcher, clock=clock
    ).execute(
        integration_session,
        account_id=account_id,
        batch=batch_of(device_id, legacy[1:]),
    )

    assert seen == ([] if segments_stored else changes(account_id, (0, 10)))
