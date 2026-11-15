"""The observation reads that analytics uses: timeline ranges and focus sessions."""

from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.devices import Device
from mosemo.labels.domain.labels import Label
from mosemo.observation.bootstrap import (
    create_focus_session_log,
    create_observation_timeline,
)
from mosemo.observation.domain.activities import ActivityRecord, RecordType
from mosemo.observation.domain.focus_sessions import FocusSession, FocusSessionPause
from mosemo.observation.domain.timeline import ActivityTimelineSegment, SegmentType
from mosemo.observation.public import FocusSessionStatus
from mosemo.shared.domain.timezones import Timezone

_sequences = count()


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


async def account_and_device(session: AsyncSession) -> tuple[UUID, UUID]:
    account = Account(
        provider=AccountProvider.KAKAO, provider_subject=f"sources-{uuid4()}"
    )
    session.add(account)
    await session.flush()
    device = Device(account_id=account.id, idempotency_key=uuid4())
    session.add(device)
    await session.flush()
    return account.id, device.id


def stored_record(device_id: UUID, observed_at: datetime) -> ActivityRecord:
    return ActivityRecord(
        event_id=uuid4(),
        device_id=device_id,
        sequence=next(_sequences),
        record_type=RecordType.ACTIVITY_OBSERVATION,
        observed_at=observed_at,
        timezone_id=Timezone.ASIA_SEOUL,
        utc_offset_minutes=540,
        payload={"context": {"kind": "opaque"}},
    )


def activity(
    session: AsyncSession,
    account_id: UUID,
    device_id: UUID,
    *,
    started_at: datetime,
    last_observed_at: datetime,
    ended_at: datetime | None,
) -> ActivityTimelineSegment:
    first = stored_record(device_id, started_at)
    last = stored_record(device_id, last_observed_at)
    session.add_all([first, last])
    segment = ActivityTimelineSegment(
        account_id=account_id,
        segment_type=SegmentType.ACTIVITY,
        started_at=started_at,
        ended_at=ended_at,
        first_event_id=first.event_id,
        last_event_id=last.event_id,
        last_observed_at=last_observed_at,
        context={"kind": "opaque"},
    )
    session.add(segment)
    return segment


def open_gap(
    session: AsyncSession, account_id: UUID, device_id: UUID, *, started_at: datetime
) -> ActivityTimelineSegment:
    first = stored_record(device_id, started_at)
    session.add(first)
    segment = ActivityTimelineSegment(
        account_id=account_id,
        segment_type=SegmentType.CAPTURE_GAP,
        started_at=started_at,
        first_event_id=first.event_id,
        reason="user_paused",
    )
    session.add(segment)
    return segment


def d1_reference(
    seeds: list[ActivityTimelineSegment], *, start: datetime, end: datetime
) -> list[UUID]:
    """D1 without the lower bound: `started_at < end` and a recorded end
    (`ended_at`, else `last_observed_at`, else infinity) at or after `start`."""
    selected = [
        segment
        for segment in seeds
        if segment.started_at < end
        and (
            segment.ended_at
            or segment.last_observed_at
            or datetime.max.replace(tzinfo=UTC)
        )
        >= start
    ]
    return [s.id for s in sorted(selected, key=lambda s: (s.started_at, s.id))]


def kst_day(year: int, month: int, day: int) -> tuple[datetime, datetime]:
    start = utc(year, month, day) - timedelta(hours=9)
    return start, start + timedelta(days=1)


@pytest.mark.asyncio
async def test_read_range_matches_d1_without_lower_bound(
    integration_session: AsyncSession,
) -> None:
    account_id, device_id = await account_and_device(integration_session)
    session = integration_session
    day_start, _ = kst_day(2026, 9, 14)
    seeds = [
        # A year earlier.
        activity(
            session,
            account_id,
            device_id,
            started_at=utc(2025, 9, 14, 1),
            last_observed_at=utc(2025, 9, 14, 1, 5),
            ended_at=utc(2025, 9, 14, 1, 10),
        ),
        activity(
            session,
            account_id,
            device_id,
            started_at=day_start - timedelta(hours=1),
            last_observed_at=day_start - timedelta(minutes=15),
            ended_at=day_start - timedelta(minutes=10),
        ),
        # Ends exactly at the day start.
        activity(
            session,
            account_id,
            device_id,
            started_at=day_start - timedelta(minutes=2),
            last_observed_at=day_start - timedelta(minutes=1),
            ended_at=day_start,
        ),
        # Zero seconds at the day start.
        activity(
            session,
            account_id,
            device_id,
            started_at=day_start,
            last_observed_at=day_start,
            ended_at=day_start,
        ),
        activity(
            session,
            account_id,
            device_id,
            started_at=utc(2026, 9, 14, 3),
            last_observed_at=utc(2026, 9, 14, 3, 59),
            ended_at=utc(2026, 9, 14, 4),
        ),
        # Crosses the next KST midnight (15:00 UTC).
        activity(
            session,
            account_id,
            device_id,
            started_at=utc(2026, 9, 14, 14, 50),
            last_observed_at=utc(2026, 9, 14, 15, 9),
            ended_at=utc(2026, 9, 14, 15, 10),
        ),
        # Open activity.
        activity(
            session,
            account_id,
            device_id,
            started_at=utc(2026, 9, 14, 16),
            last_observed_at=utc(2026, 9, 14, 16, 5),
            ended_at=None,
        ),
        open_gap(session, account_id, device_id, started_at=utc(2026, 9, 14, 17)),
    ]
    other_account, other_device = await account_and_device(session)
    activity(
        session,
        other_account,
        other_device,
        started_at=utc(2026, 9, 14, 3),
        last_observed_at=utc(2026, 9, 14, 3, 1),
        ended_at=utc(2026, 9, 14, 3, 2),
    )
    await session.flush()
    timeline = create_observation_timeline()
    now = utc(2026, 9, 14, 16, 5, 30)

    days = [(2025, 9, 14), (2026, 9, 13), (2026, 9, 14), (2026, 9, 15), (2027, 1, 1)]
    for day in days:
        start, end = kst_day(*day)
        found = await timeline.read_range(
            session, account_id, start=start, end=end, now=now
        )
        assert [s.id for s in found] == d1_reference(seeds, start=start, end=end), day

    start, end = kst_day(2026, 9, 14)
    found = await timeline.read_range(
        session, account_id, start=start, end=end, now=now
    )
    assert [s.id for s in found] == [seeds[2].id, seeds[3].id, seeds[4].id, seeds[5].id]
    start, end = kst_day(2026, 9, 15)
    found = await timeline.read_range(
        session, account_id, start=start, end=end, now=now
    )
    assert [s.id for s in found] == [seeds[5].id, seeds[6].id, seeds[7].id]
    assert found[1].ended_at is None


@pytest.mark.asyncio
async def test_list_preceding_and_find_observed_through(
    integration_session: AsyncSession,
) -> None:
    session = integration_session
    account_id, device_id = await account_and_device(session)
    timeline = create_observation_timeline()
    now = utc(2026, 9, 14, 12)

    assert await timeline.find_observed_through(session, account_id) is None
    assert await timeline.list_preceding(session, account_id, before=now, now=now) == []

    seeds = [
        activity(
            session,
            account_id,
            device_id,
            started_at=utc(2026, 9, 14, hour),
            last_observed_at=utc(2026, 9, 14, hour, 30),
            ended_at=utc(2026, 9, 14, hour, 40),
        )
        for hour in range(1, 6)
    ]
    open_gap(session, account_id, device_id, started_at=utc(2026, 9, 14, 6))
    await session.flush()

    page = await timeline.list_preceding(
        session, account_id, before=utc(2026, 9, 14, 4), limit=2, now=now
    )
    assert [s.id for s in page] == [seeds[2].id, seeds[1].id]
    everything = await timeline.list_preceding(
        session, account_id, before=utc(2026, 9, 14, 4), now=now
    )
    assert [s.id for s in everything] == [seeds[2].id, seeds[1].id, seeds[0].id]
    assert everything[0].version is not None
    assert await timeline.find_observed_through(session, account_id) == utc(
        2026, 9, 14, 5, 30
    )


def focus_session(
    account_id: UUID,
    device_id: UUID,
    *,
    started_at: datetime,
    ended_at: datetime | None = None,
    label_id: UUID | None = None,
    pauses: list[FocusSessionPause] | None = None,
) -> FocusSession:
    completed = ended_at is not None
    return FocusSession(
        id=uuid4(),
        account_id=account_id,
        device_id=device_id,
        started_at=started_at,
        ended_at=ended_at,
        target_seconds=0,
        work_seconds=0 if completed else None,
        label_id=label_id if completed else None,
        pauses_reported=pauses is not None,
        pauses=pauses or [],
    )


@pytest.mark.asyncio
async def test_focus_session_log_reads_completed_sessions_of_the_account(
    integration_session: AsyncSession,
) -> None:
    session = integration_session
    account_id, device_id = await account_and_device(session)
    other_account, other_device = await account_and_device(session)
    label = Label(account_id=account_id, display_name="코딩")
    other_label = Label(account_id=other_account, display_name="코딩")
    session.add_all([label, other_label])
    await session.flush()
    start, end = kst_day(2026, 9, 14)
    pause = FocusSessionPause(
        started_at=utc(2026, 9, 14, 1, 10), ended_at=utc(2026, 9, 14, 1, 20)
    )
    ends_at_start = focus_session(
        account_id,
        device_id,
        started_at=start - timedelta(hours=1),
        ended_at=start,
        label_id=label.id,
    )
    with_pause = focus_session(
        account_id,
        device_id,
        started_at=utc(2026, 9, 14, 1),
        ended_at=utc(2026, 9, 14, 2),
        label_id=label.id,
        pauses=[pause],
    )
    crossing_end = focus_session(
        account_id,
        device_id,
        started_at=end - timedelta(minutes=30),
        ended_at=end + timedelta(minutes=30),
        label_id=label.id,
    )
    in_progress = focus_session(account_id, device_id, started_at=utc(2026, 9, 14, 5))
    foreign = focus_session(
        other_account,
        other_device,
        started_at=utc(2026, 9, 14, 3),
        ended_at=utc(2026, 9, 14, 4),
        label_id=other_label.id,
    )
    session.add_all([ends_at_start, with_pause, crossing_end, in_progress, foreign])
    await session.flush()
    log = create_focus_session_log()

    started = await log.list_completed_started_between(
        session, account_id, start=start, end=end
    )
    assert [view.focus_session_id for view in started] == [
        with_pause.id,
        crossing_end.id,
    ]
    paused = started[0]
    assert paused.account_id == account_id
    assert paused.started_at == utc(2026, 9, 14, 1)
    assert paused.ended_at == utc(2026, 9, 14, 2)
    assert paused.label_id == label.id
    assert paused.pauses_reported
    assert paused.pauses == ((pause.started_at, pause.ended_at),)
    assert not started[1].pauses_reported
    assert started[1].pauses == ()

    overlapping = await log.list_completed_overlapping(
        session, account_id, start=start, end=end
    )
    assert [view.focus_session_id for view in overlapping] == [
        ends_at_start.id,
        with_pause.id,
        crossing_end.id,
    ]

    assert (
        await log.find_completed(session, account_id, focus_session_id=with_pause.id)
        == paused
    )
    for missing in (in_progress.id, foreign.id, uuid4()):
        assert (
            await log.find_completed(session, account_id, focus_session_id=missing)
            is None
        )

    statuses = {
        focus_session_id: await log.find_status(
            session, account_id, focus_session_id=focus_session_id
        )
        for focus_session_id in (with_pause.id, in_progress.id, foreign.id)
    }
    assert statuses == {
        with_pause.id: FocusSessionStatus.COMPLETED,
        in_progress.id: FocusSessionStatus.IN_PROGRESS,
        foreign.id: FocusSessionStatus.MISSING,
    }
    assert (
        await log.find_status(session, account_id, focus_session_id=uuid4())
        is FocusSessionStatus.MISSING
    )
