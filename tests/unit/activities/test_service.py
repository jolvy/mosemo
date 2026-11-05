import asyncio
from datetime import UTC, datetime
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.public import DeviceDirectory
from mosemo.observation.application.activities import RecordActivity
from mosemo.observation.domain.activities import (
    ActivityDeviceNotFoundError,
    ActivityEventIdConflictError,
    ActivitySequenceConflictError,
)
from mosemo.observation.domain.activities import ActivityRecord as StoredActivityRecord
from mosemo.observation.infrastructure.activity_records import (
    ActivityRecordRepository,
)
from mosemo.observation.infrastructure.timeline_segments import (
    TimelineSegmentRepository,
)
from mosemo.observation.presentation.activities.mappers import ActivityRecordMapper
from mosemo.observation.presentation.activities.schemas import (
    ActivityCreateResponse,
    ActivityRecord,
)
from mosemo.platform.clock import utc_now
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.domain.timezones import Timezone
from tests.clock_support import FakeClock
from tests.use_case_support import record_transactions

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


@pytest.fixture
def make_service(monkeypatch: pytest.MonkeyPatch):
    def make():
        transactions = record_transactions(
            monkeypatch, "mosemo.observation.application.activities", "unit_of_work"
        )
        session = create_autospec(AsyncSession, instance=True)
        repository = create_autospec(ActivityRecordRepository, instance=True)
        segments = create_autospec(TimelineSegmentRepository, instance=True)
        segments.find_replay_start.return_value = datetime(2026, 9, 14, tzinfo=UTC)
        segments.list_account_segments.return_value = []
        repository.list_account_records.return_value = []
        device_repository = create_autospec(DeviceDirectory, instance=True)
        use_case = RecordActivity(
            devices=device_repository,
            dispatcher=EventDispatcher(),
            clock=FakeClock(utc_now()),
        )
        monkeypatch.setattr(use_case, "_records", repository)
        monkeypatch.setattr(use_case, "_segments", segments)
        return use_case, session, repository, device_repository, transactions

    return make


def create_activity(
    use_case: RecordActivity,
    session: AsyncSession,
    *,
    account_id: UUID,
    record: ActivityRecord,
) -> ActivityCreateResponse:
    stored = asyncio.run(
        use_case.execute(
            session,
            account_id=account_id,
            record=ActivityRecordMapper().to_entity(record),
        )
    )
    return ActivityCreateResponse.from_record(stored)


def test_create_activity_commits_new_record(make_service) -> None:
    use_case, session, repository, device_repository, transactions = make_service()
    account_id = uuid4()
    record = make_record(sequence=2)
    stored = stored_record(record)
    device_repository.find_owned.return_value = record.device_id
    repository.get_or_create.return_value = (stored, True)

    result = create_activity(use_case, session, account_id=account_id, record=record)

    assert result.event_id == record.event_id
    assert result.status == "accepted"
    assert result.received_at == RECEIVED_AT
    repository.get_or_create.assert_awaited_once()
    candidate = repository.get_or_create.await_args.args[1]
    assert isinstance(candidate, StoredActivityRecord)
    assert candidate.has_same_content(ActivityRecordMapper().to_entity(record))
    assert (transactions.entered, transactions.committed) == (1, 1)
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_create_activity_returns_original_result_for_identical_retry(
    make_service,
) -> None:
    use_case, session, repository, device_repository, transactions = make_service()
    account_id = uuid4()
    record = make_record(sequence=5)
    existing = stored_record(record)
    device_repository.find_owned.return_value = record.device_id
    repository.get_or_create.return_value = (existing, False)

    result = create_activity(use_case, session, account_id=account_id, record=record)

    assert result.event_id == record.event_id
    assert result.received_at == RECEIVED_AT
    assert (transactions.entered, transactions.committed) == (1, 1)
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_create_activity_rejects_changed_content_for_existing_event_id(
    make_service,
) -> None:
    use_case, session, repository, device_repository, transactions = make_service()
    account_id = uuid4()
    record = make_record()
    device_repository.find_owned.return_value = record.device_id
    repository.get_or_create.return_value = (
        stored_record(record, timezone_id=Timezone.UTC),
        False,
    )

    with pytest.raises(ActivityEventIdConflictError):
        create_activity(use_case, session, account_id=account_id, record=record)

    assert (transactions.entered, transactions.committed) == (1, 0)
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_create_activity_rejects_sequence_used_by_another_event(
    make_service,
) -> None:
    use_case, session, repository, device_repository, transactions = make_service()
    account_id = uuid4()
    record = make_record()
    device_repository.find_owned.return_value = record.device_id
    repository.get_or_create.return_value = (
        stored_record(record, event_id=uuid4()),
        False,
    )

    with pytest.raises(ActivitySequenceConflictError):
        create_activity(use_case, session, account_id=account_id, record=record)

    assert (transactions.entered, transactions.committed) == (1, 0)
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_create_activity_allows_an_unused_lower_sequence(make_service) -> None:
    use_case, session, repository, device_repository, transactions = make_service()
    account_id = uuid4()
    record = make_record(sequence=2)
    device_repository.find_owned.return_value = record.device_id
    repository.get_or_create.return_value = (stored_record(record), True)

    result = create_activity(use_case, session, account_id=account_id, record=record)

    assert result.status == "accepted"
    assert (transactions.entered, transactions.committed) == (1, 1)
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_create_activity_hides_missing_and_unowned_devices(make_service) -> None:
    use_case, session, repository, device_repository, transactions = make_service()
    account_id = uuid4()
    record = make_record()
    device_repository.find_owned.return_value = None

    with pytest.raises(ActivityDeviceNotFoundError):
        create_activity(use_case, session, account_id=account_id, record=record)

    device_repository.find_owned.assert_awaited_once_with(
        session,
        account_id=account_id,
        device_id=record.device_id,
    )
    repository.get_or_create.assert_not_awaited()
    assert (transactions.entered, transactions.committed) == (1, 0)
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()
