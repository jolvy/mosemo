from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.activities.models import ActivityRecord as StoredActivityRecord
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.schemas import (
    ActivityRecord,
    ActivitySegmentResponse,
    CaptureGapResponse,
)
from mosemo.activities.service import ActivityService
from mosemo.devices.models import Device
from mosemo.devices.repository import DeviceRepository

record_adapter = TypeAdapter(ActivityRecord)


async def account_with_devices(session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    account = AccountRepository(session).save(
        provider=AccountProvider.KAKAO,
        provider_subject=f"timeline-{uuid4()}",
    )
    await session.flush()
    first = Device(account_id=account.account_id, idempotency_key=uuid4())
    second = Device(account_id=account.account_id, idempotency_key=uuid4())
    session.add_all([first, second])
    await session.flush()
    return account.account_id, first.device_id, second.device_id


def observation(
    device_id: UUID,
    sequence: int,
    observed_at: str,
    context: dict[str, object],
) -> ActivityRecord:
    return record_adapter.validate_python(
        {
            "deviceId": str(device_id),
            "eventId": str(uuid4()),
            "sequence": sequence,
            "recordType": "activity_observation",
            "observedAt": observed_at,
            "timezoneId": "Asia/Seoul",
            "utcOffsetMinutes": 540,
            "context": context,
        }
    )


def state_change(
    device_id: UUID,
    sequence: int,
    observed_at: str,
    state: str,
    reason: str,
) -> ActivityRecord:
    return record_adapter.validate_python(
        {
            "deviceId": str(device_id),
            "eventId": str(uuid4()),
            "sequence": sequence,
            "recordType": "collection_state_changed",
            "observedAt": observed_at,
            "timezoneId": "Asia/Seoul",
            "utcOffsetMinutes": 540,
            "state": state,
            "reason": reason,
        }
    )


def detailed_context() -> dict[str, object]:
    return {
        "kind": "detailed",
        "app": {
            "bundleId": {"status": "captured", "value": "com.apple.Safari"},
            "name": {"status": "captured", "value": "Safari"},
        },
        "window": {"status": "absent"},
        "web": {"kind": "not_applicable"},
    }


@pytest.mark.asyncio
async def test_activity_projection_merges_devices_and_keeps_instant_switch(
    integration_session: AsyncSession,
) -> None:
    account_id, first_device, second_device = await account_with_devices(
        integration_session
    )
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    detailed = detailed_context()
    records = [
        observation(first_device, 1, "2026-09-14T00:00:00Z", {"kind": "opaque"}),
        observation(second_device, 1, "2026-09-14T00:00:30Z", {"kind": "opaque"}),
        observation(first_device, 2, "2026-09-14T00:00:30.123456Z", detailed),
        observation(first_device, 3, "2026-09-14T00:00:30.123456Z", {"kind": "opaque"}),
    ]

    assert (
        await service.get_timeline(account_id=account_id, date=date(2026, 9, 13)) == []
    )
    for index, record in enumerate(records):
        await service.create_activity(account_id=account_id, record=record)
        if index == 2:
            await integration_session.execute(
                update(StoredActivityRecord)
                .where(StoredActivityRecord.event_id == record.event_id)
                .values(received_at=datetime(2026, 9, 14, tzinfo=UTC))
            )
            await integration_session.commit()

    segments = await service.get_timeline(account_id=account_id, date=date(2026, 9, 14))
    activity_segments = [
        segment for segment in segments if isinstance(segment, ActivitySegmentResponse)
    ]
    assert len(activity_segments) == len(segments)
    assert [segment.segment_type for segment in segments] == [
        "activity",
        "activity",
        "activity",
    ]
    assert [segment.context.kind for segment in activity_segments] == [
        "opaque",
        "detailed",
        "opaque",
    ]
    assert activity_segments[0].last_observed_at == records[1].observed_at
    assert activity_segments[0].ended_at == records[2].observed_at
    assert (
        activity_segments[1].started_at
        == activity_segments[1].ended_at
        == records[2].observed_at
    )
    assert activity_segments[2].started_at == records[3].observed_at
    assert len({segment.segment_id for segment in segments}) == 3

    same_result = await service.create_activity(
        account_id=account_id, record=records[3]
    )
    assert same_result.event_id == records[3].event_id
    retried = await service.get_timeline(account_id=account_id, date=date(2026, 9, 14))
    assert retried == segments


@pytest.mark.asyncio
async def test_silence_closes_at_last_observation_without_synthetic_gap(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    records = [
        observation(device_id, 1, "2026-09-14T00:00:00Z", {"kind": "opaque"}),
        observation(device_id, 2, "2026-09-14T00:01:00Z", {"kind": "opaque"}),
        observation(
            device_id,
            3,
            "2026-09-14T00:02:00.000001Z",
            {"kind": "opaque"},
        ),
    ]
    for record in records:
        await service.create_activity(
            account_id=account_id,
            record=record,
            now=datetime(2026, 9, 14, 0, 2, 1, tzinfo=UTC),
        )

    segments = await service.get_timeline(
        account_id=account_id,
        date=date(2026, 9, 14),
        now=datetime(2026, 9, 14, 0, 2, 1, tzinfo=UTC),
    )
    assert len(segments) == 2
    assert all(isinstance(segment, ActivitySegmentResponse) for segment in segments)
    assert isinstance(segments[0], ActivitySegmentResponse)
    assert segments[0].last_observed_at == records[1].observed_at
    assert segments[0].ended_at == records[1].observed_at
    assert segments[1].started_at == records[2].observed_at
    assert segments[1].ended_at is None
    stored = await ActivityRepository(integration_session).list_account_segments(
        account_id
    )
    assert stored[0].first_event_id == records[0].event_id
    assert stored[0].last_event_id == records[1].event_id


@pytest.mark.asyncio
async def test_old_open_activity_is_closed_on_read_and_next_write(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    record = observation(device_id, 1, "2026-09-14T00:00:00Z", {"kind": "opaque"})
    observed_at = record.observed_at
    await service.create_activity(
        account_id=account_id,
        record=record,
        now=observed_at + timedelta(seconds=59),
    )
    fresh = await service.get_timeline(
        account_id=account_id,
        date=date(2026, 9, 14),
        now=observed_at + timedelta(seconds=60),
    )
    old = await service.get_timeline(
        account_id=account_id,
        date=date(2026, 9, 14),
        now=observed_at + timedelta(seconds=61),
    )
    assert fresh[0].ended_at is None
    assert old[0].ended_at == observed_at

    await service.create_activity(
        account_id=account_id,
        record=state_change(device_id, 2, "2026-09-14T00:01:01Z", "active", "resumed"),
        now=observed_at + timedelta(seconds=61),
    )
    stored = await ActivityRepository(integration_session).list_account_segments(
        account_id
    )
    assert stored[0].ended_at == observed_at


@pytest.mark.parametrize(
    ("query_date", "observation_time", "expected_count"),
    [
        (date(2026, 3, 8), "2026-03-09T04:30:00Z", 0),
        (date(2026, 11, 1), "2026-11-02T04:30:00Z", 1),
    ],
)
@pytest.mark.asyncio
async def test_local_day_boundary_uses_dst_length(
    integration_session: AsyncSession,
    query_date: date,
    observation_time: str,
    expected_count: int,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    account = await integration_session.get(Account, account_id)
    assert account is not None
    account.timezone = "America/New_York"
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    record = observation(device_id, 1, observation_time, {"kind": "opaque"})
    await service.create_activity(
        account_id=account_id,
        record=record,
        now=record.observed_at + timedelta(seconds=10),
    )

    segments = await service.get_timeline(
        account_id=account_id,
        date=query_date,
        now=record.observed_at + timedelta(seconds=10),
    )
    assert len(segments) == expected_count


@pytest.mark.asyncio
async def test_earliest_valid_date_returns_empty_with_positive_account_offset(
    integration_session: AsyncSession,
) -> None:
    account_id, _, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )

    assert await service.get_timeline(account_id=account_id, date=date.min) == []


@pytest.mark.asyncio
async def test_stale_midnight_observation_matches_materialized_closed_span(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    before_midnight = observation(
        device_id, 1, "2026-09-15T14:59:30Z", {"kind": "opaque"}
    )
    at_midnight = observation(device_id, 2, "2026-09-15T15:00:00Z", {"kind": "opaque"})
    now = datetime(2026, 9, 15, 15, 2, tzinfo=UTC)
    for record in (before_midnight, at_midnight):
        await service.create_activity(
            account_id=account_id,
            record=record,
            now=at_midnight.observed_at,
        )

    new_date = date(2026, 9, 16)
    assert (
        await service.get_timeline(account_id=account_id, date=new_date, now=now) == []
    )

    active = state_change(device_id, 3, "2026-09-15T15:02:00Z", "active", "resume")
    await service.create_activity(account_id=account_id, record=active, now=now)
    assert (
        await service.get_timeline(account_id=account_id, date=new_date, now=now) == []
    )


@pytest.mark.asyncio
async def test_cross_midnight_segment_is_unclipped_on_both_dates(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    first = observation(device_id, 1, "2026-09-14T14:59:59Z", {"kind": "opaque"})
    second = observation(device_id, 2, "2026-09-14T15:00:01Z", detailed_context())
    now = datetime(2026, 9, 14, 15, 0, 2, tzinfo=UTC)
    for record in (first, second):
        await service.create_activity(account_id=account_id, record=record, now=now)

    yesterday = await service.get_timeline(
        account_id=account_id, date=date(2026, 9, 14), now=now
    )
    today = await service.get_timeline(
        account_id=account_id, date=date(2026, 9, 15), now=now
    )
    assert len(yesterday) == 1
    assert len(today) == 2
    assert yesterday[0].segment_id == today[0].segment_id
    assert yesterday[0].started_at == today[0].started_at == first.observed_at
    assert yesterday[0].ended_at == today[0].ended_at == second.observed_at


@pytest.mark.asyncio
async def test_instant_switch_at_local_midnight_belongs_to_new_date(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    first = observation(device_id, 1, "2026-09-14T15:00:00Z", {"kind": "opaque"})
    second = observation(device_id, 2, "2026-09-14T15:00:00Z", detailed_context())
    now = datetime(2026, 9, 14, 15, 0, 1, tzinfo=UTC)
    await service.create_activity(account_id=account_id, record=first, now=now)
    await integration_session.execute(
        update(StoredActivityRecord)
        .where(StoredActivityRecord.event_id == first.event_id)
        .values(received_at=datetime(2026, 9, 14, tzinfo=UTC))
    )
    await integration_session.commit()
    await service.create_activity(account_id=account_id, record=second, now=now)

    assert (
        await service.get_timeline(
            account_id=account_id, date=date(2026, 9, 14), now=now
        )
        == []
    )
    new_date = await service.get_timeline(
        account_id=account_id, date=date(2026, 9, 15), now=now
    )
    assert len(new_date) == 2
    assert new_date[0].started_at == new_date[0].ended_at == first.observed_at


@pytest.mark.asyncio
async def test_explicit_gap_ignores_active_and_closes_on_next_observation(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    records = [
        observation(device_id, 1, "2026-09-14T00:00:00Z", {"kind": "opaque"}),
        state_change(
            device_id, 3, "2026-09-14T00:00:30Z", "suspended", "screen_locked"
        ),
        state_change(device_id, 7, "2026-09-14T00:00:40Z", "suspended", "sleep"),
        state_change(device_id, 8, "2026-09-14T00:00:45Z", "active", "resumed"),
        observation(device_id, 9, "2026-09-14T00:00:50Z", {"kind": "opaque"}),
    ]
    now = datetime(2026, 9, 14, 0, 0, 51, tzinfo=UTC)
    for record in records:
        await service.create_activity(account_id=account_id, record=record, now=now)

    segments = await service.get_timeline(
        account_id=account_id, date=date(2026, 9, 14), now=now
    )
    assert len(segments) == 3
    first, gap, last = segments
    assert isinstance(first, ActivitySegmentResponse)
    assert isinstance(gap, CaptureGapResponse)
    assert isinstance(last, ActivitySegmentResponse)
    assert first.ended_at == gap.started_at == records[1].observed_at
    assert gap.reason == "screen_locked"
    assert gap.ended_at == last.started_at == records[4].observed_at
    assert first.context == last.context
    assert first.segment_id != last.segment_id
    stored = await ActivityRepository(integration_session).list_account_segments(
        account_id
    )
    assert stored[1].first_event_id == records[1].event_id
    assert stored[1].last_event_id is None


@pytest.mark.asyncio
async def test_old_activity_ends_before_suspended_without_inferred_gap(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    activity = observation(device_id, 1, "2026-09-14T00:00:00Z", {"kind": "opaque"})
    suspended = state_change(
        device_id,
        2,
        "2026-09-14T00:01:00.000001Z",
        "suspended",
        "screen_locked",
    )
    now = datetime(2026, 9, 14, 0, 1, 1, tzinfo=UTC)
    for record in (activity, suspended):
        await service.create_activity(account_id=account_id, record=record, now=now)

    segments = await service.get_timeline(
        account_id=account_id, date=date(2026, 9, 14), now=now
    )
    assert len(segments) == 2
    assert isinstance(segments[0], ActivitySegmentResponse)
    assert isinstance(segments[1], CaptureGapResponse)
    assert segments[0].ended_at == activity.observed_at
    assert segments[1].started_at == suspended.observed_at


@pytest.mark.asyncio
async def test_open_gap_reaches_today_but_not_future(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    await service.create_activity(
        account_id=account_id,
        record=state_change(
            device_id, 1, "2026-09-14T00:00:00Z", "suspended", "screen_locked"
        ),
        now=datetime(2026, 9, 16, 0, 0, tzinfo=UTC),
    )
    today = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
    for query_date in (date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16)):
        segments = await service.get_timeline(
            account_id=account_id, date=query_date, now=today
        )
        assert len(segments) == 1
        assert isinstance(segments[0], CaptureGapResponse)
        assert segments[0].ended_at is None
    assert (
        await service.get_timeline(
            account_id=account_id, date=date(2026, 9, 17), now=today
        )
        == []
    )


@pytest.mark.asyncio
async def test_far_future_date_is_empty_even_with_activity(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    record = observation(device_id, 1, "2026-09-14T00:00:00Z", {"kind": "opaque"})
    await service.create_activity(
        account_id=account_id, record=record, now=record.observed_at
    )

    assert (
        await service.get_timeline(
            account_id=account_id,
            date=date.max,
            now=datetime(2026, 9, 14, 0, 0, tzinfo=UTC),
        )
        == []
    )


@pytest.mark.asyncio
async def test_late_observation_matches_chronological_timeline(
    integration_session: AsyncSession,
) -> None:
    first_account, first_device, _ = await account_with_devices(integration_session)
    second_account, second_device, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    detailed = detailed_context()
    times = [
        "2026-09-14T00:00:00Z",
        "2026-09-14T00:00:30Z",
        "2026-09-14T00:00:50Z",
    ]
    contexts: list[dict[str, object]] = [
        {"kind": "opaque"},
        detailed,
        {"kind": "opaque"},
    ]
    chronological = [
        observation(first_device, index + 1, instant, contexts[index])
        for index, instant in enumerate(times)
    ]
    late = [
        observation(second_device, index + 1, instant, contexts[index])
        for index, instant in enumerate(times)
    ]
    now = datetime(2026, 9, 14, 0, 1, tzinfo=UTC)
    for record in chronological:
        await service.create_activity(account_id=first_account, record=record, now=now)
    for index in (0, 2, 1):
        await service.create_activity(
            account_id=second_account, record=late[index], now=now
        )

    first_timeline = await service.get_timeline(
        account_id=first_account, date=date(2026, 9, 14), now=now
    )
    late_timeline = await service.get_timeline(
        account_id=second_account, date=date(2026, 9, 14), now=now
    )
    assert len(first_timeline) == len(late_timeline) == 3
    assert [
        segment.model_dump(mode="json", exclude={"segment_id"})
        for segment in first_timeline
    ] == [
        segment.model_dump(mode="json", exclude={"segment_id"})
        for segment in late_timeline
    ]


@pytest.mark.asyncio
async def test_late_bridge_merges_and_keeps_earliest_segment_id(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    first = observation(device_id, 1, "2026-09-14T00:00:00Z", {"kind": "opaque"})
    last = observation(device_id, 3, "2026-09-14T00:02:00Z", {"kind": "opaque"})
    bridge = observation(device_id, 2, "2026-09-14T00:01:00Z", {"kind": "opaque"})
    now = datetime(2026, 9, 14, 0, 2, 1, tzinfo=UTC)
    for record in (first, last):
        await service.create_activity(account_id=account_id, record=record, now=now)
    before = await service.get_timeline(
        account_id=account_id, date=date(2026, 9, 14), now=now
    )
    assert len(before) == 2

    await service.create_activity(account_id=account_id, record=bridge, now=now)
    after = await service.get_timeline(
        account_id=account_id, date=date(2026, 9, 14), now=now
    )
    assert len(after) == 1
    assert after[0].segment_id == before[0].segment_id


@pytest.mark.asyncio
async def test_late_split_stops_at_unchanged_downstream_segment(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    service = ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    )
    observations = [
        observation(device_id, 1, "2026-09-14T00:00:00Z", {"kind": "opaque"}),
        observation(device_id, 2, "2026-09-14T00:00:30Z", detailed_context()),
        observation(device_id, 3, "2026-09-14T00:01:00Z", {"kind": "opaque"}),
        observation(device_id, 4, "2026-09-14T00:01:30Z", detailed_context()),
    ]
    now = datetime(2026, 9, 14, 0, 1, 31, tzinfo=UTC)
    for record in observations:
        await service.create_activity(account_id=account_id, record=record, now=now)
    before = await service.get_timeline(
        account_id=account_id, date=date(2026, 9, 14), now=now
    )
    assert len(before) == 4
    untouched_id = before[2].segment_id
    old_xmin = await integration_session.scalar(
        text(
            "SELECT CAST(xmin AS text) FROM activity_timeline_segments "
            "WHERE segment_id = :segment_id"
        ),
        {"segment_id": untouched_id},
    )
    await integration_session.commit()

    await service.create_activity(
        account_id=account_id,
        record=observation(device_id, 5, "2026-09-14T00:00:15Z", detailed_context()),
        now=now,
    )
    after = await service.get_timeline(
        account_id=account_id, date=date(2026, 9, 14), now=now
    )
    assert len(after) == 4
    assert after[0].segment_id == before[0].segment_id
    assert after[2].segment_id == untouched_id
    assert after[3].segment_id == before[3].segment_id
    new_xmin = await integration_session.scalar(
        text(
            "SELECT CAST(xmin AS text) FROM activity_timeline_segments "
            "WHERE segment_id = :segment_id"
        ),
        {"segment_id": untouched_id},
    )
    assert new_xmin == old_xmin


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"last_event_id": None},
        {"last_observed_at": None},
        {"context": None},
        {"reason": "not_an_activity_reason"},
        {"segment_type": "capture_gap"},
    ],
)
@pytest.mark.asyncio
async def test_projection_database_rejects_wrong_kind_fields(
    integration_session: AsyncSession,
    invalid_fields: dict[str, object],
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    record = observation(device_id, 1, "2026-09-14T00:00:00Z", {"kind": "opaque"})
    await ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    ).create_activity(account_id=account_id, record=record, now=record.observed_at)
    values: dict[str, object] = {
        "segment_id": uuid4(),
        "account_id": account_id,
        "segment_type": "activity",
        "started_at": record.observed_at,
        "ended_at": record.observed_at,
        "first_event_id": record.event_id,
        "last_event_id": record.event_id,
        "last_observed_at": record.observed_at,
        "context": {"kind": "opaque"},
        "reason": None,
    }
    integration_session.add(ActivityTimelineSegment(**(values | invalid_fields)))

    with pytest.raises(IntegrityError):
        await integration_session.flush()


@pytest.mark.asyncio
async def test_account_delete_cascades_raw_and_projection(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    record = observation(device_id, 1, "2026-09-14T00:00:00Z", {"kind": "opaque"})
    await ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    ).create_activity(account_id=account_id, record=record, now=record.observed_at)

    await integration_session.execute(
        delete(Account).where(Account.account_id == account_id)
    )
    await integration_session.commit()
    raw_count = await integration_session.scalar(
        select(func.count())
        .select_from(StoredActivityRecord)
        .where(StoredActivityRecord.event_id == record.event_id)
    )
    segment_count = await integration_session.scalar(
        select(func.count())
        .select_from(ActivityTimelineSegment)
        .where(ActivityTimelineSegment.account_id == account_id)
    )
    assert raw_count == segment_count == 0


@pytest.mark.asyncio
async def test_referenced_raw_event_cannot_be_deleted_without_projection_change(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id, _ = await account_with_devices(integration_session)
    await integration_session.commit()
    record = observation(device_id, 1, "2026-09-14T00:00:00Z", {"kind": "opaque"})
    await ActivityService(
        session=integration_session,
        repository=ActivityRepository(integration_session),
        device_repository=DeviceRepository(integration_session),
    ).create_activity(account_id=account_id, record=record, now=record.observed_at)
    await integration_session.execute(
        delete(StoredActivityRecord).where(
            StoredActivityRecord.event_id == record.event_id
        )
    )

    with pytest.raises(IntegrityError):
        await integration_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
