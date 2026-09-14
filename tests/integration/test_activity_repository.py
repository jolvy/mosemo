import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.activities.models import ActivityRecord as StoredActivityRecord
from mosemo.activities.models import DeviceRegistration
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.schemas import ActivityRecord
from mosemo.activities.service import (
    ActivityEventIdConflictError,
    ActivitySequenceConflictError,
    ActivityService,
)

activity_record_adapter = TypeAdapter(ActivityRecord)


def activity_observation(
    *,
    device_registration_id: UUID,
    event_id: UUID | None = None,
    sequence: int,
) -> ActivityRecord:
    return activity_record_adapter.validate_python(
        {
            "deviceRegistrationId": str(device_registration_id),
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
    device_registration_id: UUID,
    sequence: int,
) -> ActivityRecord:
    return activity_record_adapter.validate_python(
        {
            "deviceRegistrationId": str(device_registration_id),
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
) -> tuple[Account, DeviceRegistration]:
    account = AccountRepository(session).save(
        provider=AccountProvider.KAKAO,
        provider_subject=f"activity-integration-{uuid4()}",
    )
    await session.flush()
    device = DeviceRegistration(account_id=account.account_id)
    session.add(device)
    await session.flush()
    return account, device


@pytest.mark.asyncio
async def test_activity_storage_persists_both_payloads_and_allows_lower_sequence(
    integration_session: AsyncSession,
) -> None:
    account, device = await create_account_and_device(integration_session)
    other_account = AccountRepository(integration_session).save(
        provider=AccountProvider.KAKAO,
        provider_subject=f"activity-other-{uuid4()}",
    )
    await integration_session.flush()
    account_id = account.account_id
    other_account_id = other_account.account_id
    device_registration_id = device.device_registration_id
    repository = ActivityRepository(integration_session)
    service = ActivityService(session=integration_session, repository=repository)
    observation = activity_observation(
        device_registration_id=device_registration_id,
        sequence=10,
    )
    state_change = collection_state_changed(
        device_registration_id=device_registration_id,
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

    changed_event = observation.model_copy(update={"timezone_id": "UTC"})
    with pytest.raises(ActivityEventIdConflictError):
        await service.create_activity(account_id=account_id, record=changed_event)

    reused_sequence = activity_observation(
        device_registration_id=device_registration_id,
        sequence=observation.sequence,
    )
    with pytest.raises(ActivitySequenceConflictError):
        await service.create_activity(account_id=account_id, record=reused_sequence)

    stored_observation = await repository.find_by_event_id(observation.event_id)
    stored_state = await repository.find_by_event_id(state_change.event_id)
    assert stored_observation is not None
    assert stored_state is not None
    assert stored_observation.payload == {"context": {"kind": "opaque"}}
    assert stored_state.payload == {
        "state": "suspended",
        "reason": "screen_locked",
    }
    assert observation_response.status == "accepted"
    assert state_response.status == "accepted"
    assert retry_response == observation_response
    assert (
        await repository.find_owned_device(
            account_id=account_id,
            device_registration_id=device_registration_id,
        )
        is not None
    )
    assert (
        await repository.find_owned_device(
            account_id=other_account_id,
            device_registration_id=device_registration_id,
        )
        is None
    )
    count = await integration_session.scalar(
        select(func.count())
        .select_from(StoredActivityRecord)
        .where(StoredActivityRecord.device_registration_id == device_registration_id)
    )
    assert count == 2


@pytest.mark.parametrize(
    ("sequence", "record_type", "duplicate_sequence"),
    [
        (-1, "activity_observation", False),
        (1, "unsupported", False),
        (1, "activity_observation", True),
    ],
)
@pytest.mark.asyncio
async def test_activity_record_database_constraints(
    integration_session: AsyncSession,
    sequence: int,
    record_type: str,
    duplicate_sequence: bool,
) -> None:
    _, device = await create_account_and_device(integration_session)
    common_values = {
        "device_registration_id": device.device_registration_id,
        "observed_at": datetime(2026, 9, 14, tzinfo=UTC),
        "timezone_id": "Asia/Seoul",
        "utc_offset_minutes": 540,
        "payload": {"context": {"kind": "opaque"}},
    }
    if duplicate_sequence:
        integration_session.add(
            StoredActivityRecord(
                event_id=uuid4(),
                sequence=sequence,
                record_type="activity_observation",
                **common_values,
            )
        )
        await integration_session.flush()

    integration_session.add(
        StoredActivityRecord(
            event_id=uuid4(),
            sequence=sequence,
            record_type=record_type,
            **common_values,
        )
    )

    with pytest.raises(IntegrityError):
        await integration_session.flush()


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
            account_id = account.account_id
            device_registration_id = device.device_registration_id
            await setup_session.commit()

        record = activity_observation(
            device_registration_id=device_registration_id,
            sequence=7,
        )

        async def ingest():
            async with session_factory() as session:
                return await ActivityService(
                    session=session,
                    repository=ActivityRepository(session),
                ).create_activity(account_id=account.account_id, record=record)

        first, second = await asyncio.gather(ingest(), ingest())

        assert first == second
        async with session_factory() as verification_session:
            count = await verification_session.scalar(
                select(func.count())
                .select_from(StoredActivityRecord)
                .where(StoredActivityRecord.event_id == record.event_id)
            )
            assert count == 1
    finally:
        if account_id is not None:
            async with session_factory() as cleanup_session:
                await cleanup_session.execute(
                    delete(Account).where(Account.account_id == account_id)
                )
                await cleanup_session.commit()
        await engine.dispose()
