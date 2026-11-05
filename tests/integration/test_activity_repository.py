import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mosemo.identity.bootstrap import (
    create_account_directory,
    create_device_directory,
)
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.devices import Device
from mosemo.observation.domain.activities import (
    ActivityDeviceNotFoundError,
    ActivityEventIdConflictError,
    ActivitySequenceConflictError,
    RecordType,
)
from mosemo.observation.domain.activities import ActivityRecord as StoredActivityRecord
from mosemo.observation.domain.timeline import ActivityTimelineBusyError
from mosemo.observation.infrastructure.activity_records import ActivityRecordRepository
from mosemo.observation.infrastructure.tables import activity_records
from mosemo.observation.infrastructure.timeline_segments import (
    activity_timeline_lock_key,
)
from mosemo.observation.presentation.activities.mappers import ActivityRecordMapper
from mosemo.observation.presentation.activities.schemas import ActivityRecord
from mosemo.platform.clock import utc_now
from mosemo.shared.domain.timezones import Timezone
from tests.clock_support import FakeClock
from tests.identity_support import delete_accounts
from tests.observation_support import ActivityService

activity_record_adapter = TypeAdapter(ActivityRecord)


def activity_observation(
    *,
    device_id: UUID,
    event_id: UUID | None = None,
    sequence: int,
) -> ActivityRecord:
    return activity_record_adapter.validate_python(
        {
            "deviceId": str(device_id),
            "eventId": str(event_id or uuid4()),
            "sequence": sequence,
            "recordType": "activity_observation",
            "observedAt": "2026-09-14T00:00:00.123456Z",
            "timezoneId": "Asia/Seoul",
            "utcOffsetMinutes": 540,
            "context": {"kind": "opaque"},
        }
    )


def collection_state_changed(
    *,
    device_id: UUID,
    sequence: int,
) -> ActivityRecord:
    return activity_record_adapter.validate_python(
        {
            "deviceId": str(device_id),
            "eventId": str(uuid4()),
            "sequence": sequence,
            "recordType": "collection_state_changed",
            "observedAt": "2026-09-14T00:01:00Z",
            "timezoneId": "Asia/Seoul",
            "utcOffsetMinutes": 540,
            "state": "suspended",
            "reason": "screen_locked",
        }
    )


async def create_account_and_device(
    session: AsyncSession,
) -> tuple[Account, Device]:
    account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject=f"activity-integration-{uuid4()}",
    )
    session.add(account)
    await session.flush()
    device = Device(
        account_id=account.id,
        idempotency_key=uuid4(),
    )
    session.add(device)
    await session.flush()
    return account, device


@pytest.mark.asyncio
async def test_activity_storage_persists_both_payloads_and_allows_lower_sequence(
    integration_session: AsyncSession,
) -> None:
    account, device = await create_account_and_device(integration_session)
    other_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject=f"activity-other-{uuid4()}",
    )
    integration_session.add(other_account)
    await integration_session.flush()
    account_id = account.id
    other_account_id = other_account.id
    device_id = device.id
    await integration_session.commit()
    device_repository = create_device_directory()
    service = ActivityService(
        session=integration_session,
        devices=device_repository,
        accounts=create_account_directory(),
        clock=FakeClock(utc_now()),
    )
    observation = activity_observation(
        device_id=device_id,
        sequence=10,
    )
    state_change = collection_state_changed(
        device_id=device_id,
        sequence=2,
    )

    observation_response = await service.create_activity(
        account_id=account_id,
        record=observation,
    )
    state_response = await service.create_activity(
        account_id=account_id,
        record=state_change,
    )
    retry_response = await service.create_activity(
        account_id=account_id,
        record=observation,
    )

    changed_event = observation.model_copy(update={"timezone_id": Timezone.UTC})
    with pytest.raises(ActivityEventIdConflictError):
        await service.create_activity(account_id=account_id, record=changed_event)

    reused_sequence = activity_observation(
        device_id=device_id,
        sequence=observation.sequence,
    )
    with pytest.raises(ActivitySequenceConflictError):
        await service.create_activity(account_id=account_id, record=reused_sequence)

    stored_observation = await ActivityRecordRepository().find_by_event_id(
        integration_session, observation.event_id
    )
    stored_state = await ActivityRecordRepository().find_by_event_id(
        integration_session, state_change.event_id
    )
    assert stored_observation is not None
    assert stored_state is not None
    await integration_session.refresh(
        stored_observation, ["record_type", "timezone_id"]
    )
    await integration_session.refresh(stored_state, ["record_type"])
    assert stored_observation.record_type is RecordType.ACTIVITY_OBSERVATION
    assert stored_state.record_type is RecordType.COLLECTION_STATE_CHANGED
    assert stored_observation.timezone_id is Timezone.ASIA_SEOUL
    assert stored_observation.payload == {"context": {"kind": "opaque"}}
    assert stored_state.payload == {
        "state": "suspended",
        "reason": "screen_locked",
    }
    assert observation_response.status == "accepted"
    assert state_response.status == "accepted"
    assert retry_response == observation_response
    assert (
        await device_repository.find_owned(
            integration_session,
            account_id=account_id,
            device_id=device_id,
        )
        is not None
    )
    assert (
        await device_repository.find_owned(
            integration_session,
            account_id=other_account_id,
            device_id=device_id,
        )
        is None
    )
    count = await integration_session.scalar(
        select(func.count())
        .select_from(StoredActivityRecord)
        .where(activity_records.c.device_id == device_id)
    )
    assert count == 2


@pytest.mark.parametrize(
    ("sequence", "duplicate_sequence"),
    [
        (-1, False),
        (1, True),
    ],
)
@pytest.mark.asyncio
async def test_activity_record_database_constraints(
    integration_session: AsyncSession,
    sequence: int,
    duplicate_sequence: bool,
) -> None:
    _, device = await create_account_and_device(integration_session)
    common_values: dict[str, Any] = {
        "device_id": device.id,
        "observed_at": datetime(2026, 9, 14, tzinfo=UTC),
        "timezone_id": Timezone.ASIA_SEOUL,
        "utc_offset_minutes": 540,
        "payload": {"context": {"kind": "opaque"}},
    }
    if duplicate_sequence:
        integration_session.add(
            StoredActivityRecord(
                event_id=uuid4(),
                sequence=sequence,
                record_type=RecordType.ACTIVITY_OBSERVATION,
                **common_values,
            )
        )
        await integration_session.flush()

    integration_session.add(
        StoredActivityRecord(
            event_id=uuid4(),
            sequence=sequence,
            record_type=RecordType.ACTIVITY_OBSERVATION,
            **common_values,
        )
    )

    with pytest.raises(IntegrityError):
        await integration_session.flush()


@pytest.mark.asyncio
async def test_activity_record_database_rejects_unsupported_type(
    integration_session: AsyncSession,
) -> None:
    _, device = await create_account_and_device(integration_session)

    with pytest.raises(IntegrityError):
        await integration_session.execute(
            text(
                "INSERT INTO activity_records "
                "(event_id, device_id, sequence, record_type, observed_at, "
                "timezone_id, utc_offset_minutes, payload) "
                "VALUES (:event_id, :device_id, 1, :record_type, :observed_at, "
                ":timezone_id, 540, CAST(:payload AS jsonb))"
            ),
            {
                "event_id": uuid4(),
                "device_id": device.id,
                "record_type": "unsupported",
                "observed_at": datetime(2026, 9, 14, tzinfo=UTC),
                "timezone_id": Timezone.ASIA_SEOUL.value,
                "payload": '{"context":{"kind":"opaque"}}',
            },
        )


@pytest.mark.asyncio
async def test_activity_service_hides_missing_and_unowned_devices(
    integration_session: AsyncSession,
) -> None:
    owner, device = await create_account_and_device(integration_session)
    other_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject=f"activity-unowned-{uuid4()}",
    )
    integration_session.add(other_account)
    await integration_session.commit()
    owner_id = owner.id
    other_account_id = other_account.id
    device_id = device.id
    service = ActivityService(
        session=integration_session,
        devices=create_device_directory(),
        accounts=create_account_directory(),
        clock=FakeClock(utc_now()),
    )

    cases = (
        (owner_id, uuid4()),
        (other_account_id, device_id),
    )
    for account_id, requested_device_id in cases:
        record = activity_observation(device_id=requested_device_id, sequence=1)
        with pytest.raises(ActivityDeviceNotFoundError):
            await service.create_activity(account_id=account_id, record=record)

    count = await integration_session.scalar(select(func.count()).select_from(Device))
    assert count == 1


@pytest.mark.asyncio
async def test_concurrent_identical_requests_store_one_record(
    integration_database_url: str,
) -> None:
    engine = create_async_engine(integration_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    account_id: UUID | None = None
    try:
        async with session_factory() as setup_session:
            account, device = await create_account_and_device(setup_session)
            account_id = account.id
            device_id = device.id
            await setup_session.commit()

        record = activity_observation(
            device_id=device_id,
            sequence=7,
        )

        fake_clock = FakeClock(utc_now())

        async def ingest():
            async with session_factory() as session:
                return await ActivityService(
                    session=session,
                    devices=create_device_directory(),
                    accounts=create_account_directory(),
                    clock=fake_clock,
                ).create_activity(account_id=account.id, record=record)

        first, second = await asyncio.gather(ingest(), ingest())

        assert first == second
        async with session_factory() as verification_session:
            count = await verification_session.scalar(
                select(func.count())
                .select_from(StoredActivityRecord)
                .where(activity_records.c.event_id == record.event_id)
            )
            assert count == 1
    finally:
        if account_id is not None:
            async with session_factory() as cleanup_session:
                await cleanup_session.execute(delete_accounts(account_id))
                await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_account_lock_timeout_rolls_back_without_blocking_other_account(
    integration_database_url: str,
) -> None:
    engine = create_async_engine(integration_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    account_ids: list[UUID] = []
    try:
        async with session_factory() as setup_session:
            locked_account, locked_device = await create_account_and_device(
                setup_session
            )
            other_account, other_device = await create_account_and_device(setup_session)
            account_ids = [locked_account.id, other_account.id]
            await setup_session.commit()

        locked_record = activity_observation(
            device_id=locked_device.id,
            sequence=1,
        )
        other_record = activity_observation(
            device_id=other_device.id,
            sequence=1,
        )

        fake_clock = FakeClock(utc_now())

        async def ingest(account_id: UUID, record: ActivityRecord):
            async with session_factory() as session:
                return await ActivityService(
                    session=session,
                    devices=create_device_directory(),
                    accounts=create_account_directory(),
                    clock=fake_clock,
                ).create_activity(account_id=account_id, record=record)

        async with session_factory() as holder, holder.begin():
            await holder.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": activity_timeline_lock_key(locked_account.id)},
            )
            assert (await ingest(other_account.id, other_record)).status == "accepted"
            with pytest.raises(ActivityTimelineBusyError):
                await ingest(locked_account.id, locked_record)

        async with session_factory() as verification_session:
            count = await verification_session.scalar(
                select(func.count())
                .select_from(StoredActivityRecord)
                .where(activity_records.c.event_id == locked_record.event_id)
            )
            assert count == 0

        assert (await ingest(locked_account.id, locked_record)).status == "accepted"
    finally:
        if account_ids:
            async with session_factory() as cleanup_session:
                await cleanup_session.execute(delete_accounts(*account_ids))
                await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_or_create_resolves_conflicts_by_event_id_then_device_sequence(
    integration_session: AsyncSession,
) -> None:
    _, device = await create_account_and_device(integration_session)
    repository = ActivityRecordRepository()
    mapper = ActivityRecordMapper()
    first = mapper.to_entity(activity_observation(device_id=device.id, sequence=1))
    second = mapper.to_entity(activity_observation(device_id=device.id, sequence=2))

    stored, created = await repository.get_or_create(integration_session, first)
    assert created
    assert stored.event_id == first.event_id
    assert stored.received_at is not None
    await repository.get_or_create(integration_session, second)

    # Same event ID: the record with that event ID comes back, whatever else differs.
    retried = mapper.to_entity(
        activity_observation(device_id=device.id, event_id=first.event_id, sequence=9)
    )
    existing, created = await repository.get_or_create(integration_session, retried)
    assert not created
    assert existing.event_id == first.event_id
    # Both keys taken by different records: the event ID match wins.
    both = mapper.to_entity(
        activity_observation(device_id=device.id, event_id=first.event_id, sequence=2)
    )
    existing, created = await repository.get_or_create(integration_session, both)
    assert not created
    assert existing.event_id == first.event_id
    # New event ID on a taken device sequence: the sequence holder comes back.
    reused = mapper.to_entity(activity_observation(device_id=device.id, sequence=2))
    existing, created = await repository.get_or_create(integration_session, reused)
    assert not created
    assert existing.event_id == second.event_id


@pytest.mark.asyncio
async def test_get_or_create_raises_when_no_conflicting_record_is_found(
    integration_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, device = await create_account_and_device(integration_session)
    repository = ActivityRecordRepository()
    record = ActivityRecordMapper().to_entity(
        activity_observation(device_id=device.id, sequence=1)
    )
    await repository.get_or_create(integration_session, record)

    async def missing(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(repository, "find_by_event_id", missing)
    monkeypatch.setattr(repository, "find_by_device_sequence", missing)

    with pytest.raises(RuntimeError, match="could not be resolved"):
        await repository.get_or_create(integration_session, record)
