import asyncio
from datetime import UTC, datetime
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.mappers import ActivityRecordMapper
from mosemo.activities.models import ActivityRecord as StoredActivityRecord
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.schemas import ActivityRecord
from mosemo.activities.service import (
    ActivityDeviceNotFoundError,
    ActivityEventIdConflictError,
    ActivitySequenceConflictError,
    ActivityService,
)
from mosemo.clock import utc_now
from mosemo.devices.models import Device
from mosemo.devices.repository import DeviceRepository
from mosemo.timezones import Timezone
from tests.clock_support import FakeClock

activity_record_adapter = TypeAdapter(ActivityRecord)
RECEIVED_AT = datetime(2026, 9, 14, 1, 2, 3, tzinfo=UTC)


def make_record(
    *,
    device_id: UUID | None = None,
    event_id: UUID | None = None,
    sequence: int = 3,
) -> ActivityRecord:
    return activity_record_adapter.validate_python(
        {
            "deviceId": str(device_id or uuid4()),
            "eventId": str(event_id or uuid4()),
            "sequence": sequence,
            "recordType": "activity_observation",
            "observedAt": "2026-09-14T00:00:00.123456Z",
            "timezoneId": "Asia/Seoul",
            "utcOffsetMinutes": 540,
            "context": {"kind": "opaque"},
        }
    )


def stored_record(record: ActivityRecord, **changes: object) -> StoredActivityRecord:
    stored = ActivityRecordMapper().to_entity(record)
    stored.received_at = RECEIVED_AT
    for field, value in changes.items():
        setattr(stored, field, value)
    return stored


def make_service():
    session = create_autospec(AsyncSession, instance=True)
    repository = create_autospec(ActivityRepository, instance=True)
    device_repository = create_autospec(DeviceRepository, instance=True)
    service = ActivityService(
        session=session,
        repository=repository,
        device_repository=device_repository,
        clock=FakeClock(utc_now()),
    )
    return service, session, repository, device_repository


def test_create_activity_commits_new_record() -> None:
    service, session, repository, device_repository = make_service()
    account_id = uuid4()
    record = make_record(sequence=2)
    stored = stored_record(record)
    device_repository.find_owned_by_id.return_value = Device(
        device_id=record.device_id,
        account_id=account_id,
        idempotency_key=uuid4(),
    )
    repository.add_if_absent.return_value = stored

    result = asyncio.run(service.create_activity(account_id=account_id, record=record))

    assert result.event_id == record.event_id
    assert result.status == "accepted"
    assert result.received_at == RECEIVED_AT
    repository.add_if_absent.assert_awaited_once()
    candidate = repository.add_if_absent.await_args.args[0]
    assert isinstance(candidate, StoredActivityRecord)
    assert candidate.has_same_content(ActivityRecordMapper().to_entity(record))
    session.begin.assert_called_once_with()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_create_activity_returns_original_result_for_identical_retry() -> None:
    service, session, repository, device_repository = make_service()
    account_id = uuid4()
    record = make_record(sequence=5)
    existing = stored_record(record)
    device_repository.find_owned_by_id.return_value = Device(
        device_id=record.device_id,
        account_id=account_id,
        idempotency_key=uuid4(),
    )
    repository.add_if_absent.return_value = None
    repository.find_by_event_id.return_value = existing

    result = asyncio.run(service.create_activity(account_id=account_id, record=record))

    assert result.event_id == record.event_id
    assert result.received_at == RECEIVED_AT
    repository.find_by_device_sequence.assert_not_awaited()
    session.begin.assert_called_once_with()
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_create_activity_rejects_changed_content_for_existing_event_id() -> None:
    service, session, repository, device_repository = make_service()
    account_id = uuid4()
    record = make_record()
    device_repository.find_owned_by_id.return_value = Device(
        device_id=record.device_id,
        account_id=account_id,
        idempotency_key=uuid4(),
    )
    repository.add_if_absent.return_value = None
    repository.find_by_event_id.return_value = stored_record(
        record,
        timezone_id=Timezone.UTC,
    )

    with pytest.raises(ActivityEventIdConflictError):
        asyncio.run(service.create_activity(account_id=account_id, record=record))

    repository.find_by_device_sequence.assert_not_awaited()
    session.begin.assert_called_once_with()
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_create_activity_rejects_sequence_used_by_another_event() -> None:
    service, session, repository, device_repository = make_service()
    account_id = uuid4()
    record = make_record()
    device_repository.find_owned_by_id.return_value = Device(
        device_id=record.device_id,
        account_id=account_id,
        idempotency_key=uuid4(),
    )
    repository.add_if_absent.return_value = None
    repository.find_by_event_id.return_value = None
    repository.find_by_device_sequence.return_value = stored_record(
        record,
        event_id=uuid4(),
    )

    with pytest.raises(ActivitySequenceConflictError):
        asyncio.run(service.create_activity(account_id=account_id, record=record))

    repository.find_by_device_sequence.assert_awaited_once_with(
        device_id=record.device_id,
        sequence=record.sequence,
    )
    session.begin.assert_called_once_with()
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_create_activity_allows_an_unused_lower_sequence() -> None:
    service, session, repository, device_repository = make_service()
    account_id = uuid4()
    record = make_record(sequence=2)
    device_repository.find_owned_by_id.return_value = Device(
        device_id=record.device_id,
        account_id=account_id,
        idempotency_key=uuid4(),
    )
    repository.add_if_absent.return_value = stored_record(record)

    result = asyncio.run(service.create_activity(account_id=account_id, record=record))

    assert result.status == "accepted"
    repository.find_by_device_sequence.assert_not_awaited()
    session.begin.assert_called_once_with()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_create_activity_hides_missing_and_unowned_devices() -> None:
    service, session, repository, device_repository = make_service()
    account_id = uuid4()
    record = make_record()
    device_repository.find_owned_by_id.return_value = None

    with pytest.raises(ActivityDeviceNotFoundError):
        asyncio.run(service.create_activity(account_id=account_id, record=record))

    device_repository.find_owned_by_id.assert_awaited_once_with(
        account_id=account_id,
        device_id=record.device_id,
    )
    repository.add_if_absent.assert_not_awaited()
    session.begin.assert_called_once_with()
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()
