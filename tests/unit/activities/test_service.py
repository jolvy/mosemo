import asyncio
from datetime import UTC, datetime
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.models import ActivityRecord as StoredActivityRecord
from mosemo.activities.repository import (
    ActivityRepository,
    activity_payload,
    is_same_activity_record,
)
from mosemo.activities.schemas import ActivityRecord
from mosemo.activities.service import (
    ActivityDeviceNotFoundError,
    ActivityEventIdConflictError,
    ActivitySequenceConflictError,
    ActivityService,
)
from mosemo.devices.models import Device
from mosemo.devices.repository import DeviceRepository
from mosemo.timezones import Timezone

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
    values = {
        "event_id": record.event_id,
        "device_id": record.device_id,
        "sequence": record.sequence,
        "record_type": record.record_type,
        "observed_at": record.observed_at,
        "timezone_id": record.timezone_id,
        "utc_offset_minutes": record.utc_offset_minutes,
        "payload": activity_payload(record),
        "received_at": RECEIVED_AT,
        **changes,
    }
    return StoredActivityRecord(**values)


def make_service():
    session = create_autospec(AsyncSession, instance=True)
    repository = create_autospec(ActivityRepository, instance=True)
    device_repository = create_autospec(DeviceRepository, instance=True)
    service = ActivityService(
        session=session,
        repository=repository,
        device_repository=device_repository,
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
    repository.insert.return_value = stored

    result = asyncio.run(service.create_activity(account_id=account_id, record=record))

    assert result.event_id == record.event_id
    assert result.status == "accepted"
    assert result.received_at == RECEIVED_AT
    repository.insert.assert_awaited_once_with(record)
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
    repository.insert.return_value = None
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
    repository.insert.return_value = None
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
    repository.insert.return_value = None
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
    repository.insert.return_value = stored_record(record)

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
    repository.insert.assert_not_awaited()
    session.begin.assert_called_once_with()
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "different_value"),
    [
        ("event_id", uuid4()),
        ("device_id", uuid4()),
        ("sequence", 99),
        ("record_type", "collection_state_changed"),
        ("observed_at", datetime(2026, 9, 14, 2, tzinfo=UTC)),
        ("timezone_id", Timezone.UTC),
        ("utc_offset_minutes", 0),
        ("payload", {"context": {"kind": "opaque", "unexpected": True}}),
    ],
)
def test_activity_record_identity_compares_every_stored_request_field(
    field: str,
    different_value: object,
) -> None:
    record = make_record()

    assert is_same_activity_record(stored_record(record), record)
    assert not is_same_activity_record(
        stored_record(record, **{field: different_value}),
        record,
    )


def test_activity_payload_keeps_nested_public_field_names() -> None:
    record = activity_record_adapter.validate_python(
        {
            "deviceId": str(uuid4()),
            "eventId": str(uuid4()),
            "sequence": 3,
            "recordType": "activity_observation",
            "observedAt": "2026-09-14T00:00:00Z",
            "timezoneId": "Asia/Seoul",
            "utcOffsetMinutes": 540,
            "context": {
                "kind": "detailed",
                "app": {
                    "bundleId": {"status": "captured", "value": "com.apple.Safari"},
                    "name": {"status": "captured", "value": "Safari"},
                },
                "window": {
                    "status": "captured",
                    "title": {
                        "status": "captured",
                        "value": "Mosemo",
                        "truncated": True,
                        "originalByteLength": 12,
                    },
                },
                "web": {"kind": "not_applicable"},
            },
        }
    )

    payload = activity_payload(record)

    assert payload == {
        "context": {
            "kind": "detailed",
            "app": {
                "bundleId": {
                    "status": "captured",
                    "value": "com.apple.Safari",
                },
                "name": {"status": "captured", "value": "Safari"},
            },
            "window": {
                "status": "captured",
                "title": {
                    "status": "captured",
                    "value": "Mosemo",
                    "truncated": True,
                    "originalByteLength": 12,
                },
            },
            "web": {"kind": "not_applicable"},
        }
    }
