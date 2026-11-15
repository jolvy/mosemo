"""`DailyStatsMarking` through the source use cases and each assembly root."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.domain.markers import MarkerKind, MarkScope
from mosemo.analytics.infrastructure.tables import analytics_daily_markers
from mosemo.entrypoints.api import contexts as api_root
from mosemo.entrypoints.batch import registry as batch_root
from mosemo.entrypoints.consumer import activity_batches as consumer_root
from mosemo.identity.bootstrap import (
    create_account_directory,
    create_device_directory,
)
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.devices import Device
from mosemo.labeling.application.confirmations import LabelSelection
from mosemo.labeling.bootstrap import Labeling
from mosemo.labeling.public import LabelConfirmed
from mosemo.labels.bootstrap import create_label_catalog
from mosemo.labels.domain.labels import Label
from mosemo.observation.application.activities import RecordActivity
from mosemo.observation.bootstrap import (
    Observation,
    create_observation_timeline,
    create_process_activity_batch,
)
from mosemo.observation.domain.activities import ActivityRecord, RecordType
from mosemo.observation.domain.focus_sessions import (
    FocusSession,
    FocusSessionCompletion,
)
from mosemo.observation.presentation.activities.schemas import ActivityBatchRequest
from mosemo.observation.presentation.messages import ActivityRegistrationRequested
from mosemo.observation.public import FocusSessionCompleted, TimelineChanged
from mosemo.platform.database.transactions import record_event, unit_of_work
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.domain.timezones import Timezone
from tests.support.clock import FakeClock

SEOUL = Timezone.ASIA_SEOUL
NOW = datetime(2026, 11, 10, tzinfo=UTC)
OPAQUE: dict[str, object] = {"kind": "opaque"}
ROOTS: dict[str, Callable[[EventDispatcher], None]] = {
    "api": api_root.register_analytics_handlers,
    "consumer": consumer_root.register_analytics_handlers,
    "batch": batch_root.register_analytics_handlers,
}


def kst(day: int, hour: int, minute: int = 0) -> datetime:
    """2026-11-`day` `hour`:`minute` in Seoul, as UTC."""
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC) - timedelta(hours=9)


def nov(day: int) -> date:
    return date(2026, 11, day)


def detailed(bundle_id: str) -> dict[str, object]:
    return {
        "kind": "detailed",
        "app": {
            "bundleId": {"status": "captured", "value": bundle_id},
            "name": {"status": "captured", "value": bundle_id},
        },
        "window": {"status": "absent"},
        "web": {"kind": "not_applicable"},
    }


def activity_json(
    sequence: int, observed_at: datetime, context: dict[str, object]
) -> dict[str, object]:
    return {
        "eventId": str(uuid4()),
        "sequence": sequence,
        "recordType": "activity_observation",
        "observedAt": observed_at.isoformat().replace("+00:00", "Z"),
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "context": context,
    }


def activity(
    device_id: UUID, sequence: int, observed_at: datetime, context: dict[str, object]
) -> ActivityRecord:
    return ActivityRecord(
        event_id=uuid4(),
        device_id=device_id,
        sequence=sequence,
        record_type=RecordType.ACTIVITY_OBSERVATION,
        observed_at=observed_at,
        timezone_id=SEOUL,
        utc_offset_minutes=540,
        payload={"context": context},
    )


async def seoul_account(session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    account = Account(provider=AccountProvider.KAKAO, provider_subject=str(uuid4()))
    session.add(account)
    await session.flush()
    device = Device(account_id=account.id, idempotency_key=uuid4())
    label = Label(account_id=account.id, display_name="집중")
    session.add_all([device, label])
    await session.commit()
    return account.id, device.id, label.id


def dispatcher_of(root: str) -> EventDispatcher:
    dispatcher = EventDispatcher()
    ROOTS[root](dispatcher)
    return dispatcher


async def marked(session: AsyncSession, account_id: UUID) -> dict[date, dict]:
    rows = (
        await session.execute(
            select(analytics_daily_markers).where(
                analytics_daily_markers.c.account_id == account_id
            )
        )
    ).mappings()
    found = {row["local_date"]: dict(row) for row in rows}
    await session.commit()
    return found


def versions(markers: dict[date, dict]) -> dict[date, int]:
    return {day: row["version"] for day, row in markers.items()}


def assert_fresh_source_marks(markers: dict[date, dict]) -> None:
    for day, row in markers.items():
        assert row["kind"] == MarkerKind.SOURCE
        assert row["attempts"] == 0
        assert row["eligible_at"] == MarkScope.eligible_at(day, SEOUL)
        assert row["next_attempt_at"] == row["marked_at"]
        assert (row["last_error"], row["last_failed_at"]) == (None, None)


@pytest.mark.parametrize("root", sorted(ROOTS))
def test_each_root_registration_subscribes_the_three_source_events(root) -> None:
    dispatcher = dispatcher_of(root)

    assert {
        TimelineChanged,
        LabelConfirmed,
        FocusSessionCompleted,
    } <= dispatcher._handlers.keys()


@pytest.mark.asyncio
async def test_session_start_date_is_marked_again_by_a_correction_in_its_window(
    integration_session: AsyncSession,
) -> None:
    """F-4: session 11-01 23:30~11-02 00:40 (Seoul), correction at 11-02 00:10."""
    session = integration_session
    account_id, device_id, label_id = await seoul_account(session)
    dispatcher = dispatcher_of("api")
    clock = FakeClock(NOW)
    record = RecordActivity(
        devices=create_device_directory(), dispatcher=dispatcher, clock=clock
    )
    # A closed segment [00:10, 00:20] on 11-02 inside the session window.
    for sequence, observed_at, context in [
        (1, kst(2, 0, 10), detailed("com.example.a")),
        (2, kst(2, 0, 15), detailed("com.example.a")),
        (3, kst(2, 0, 20), OPAQUE),
    ]:
        await record.execute(
            session,
            account_id=account_id,
            record=activity(device_id, sequence, observed_at, context),
        )
    before_session = versions(await marked(session, account_id))
    assert set(before_session) == {nov(2)}

    focus = FocusSession(
        id=uuid4(),
        account_id=account_id,
        device_id=device_id,
        started_at=kst(1, 23, 30),
        target_seconds=0,
    )
    session.add(focus)
    await session.commit()
    observation = Observation(
        accounts=create_account_directory(),
        devices=create_device_directory(),
        labels=create_label_catalog(),
        dispatcher=dispatcher,
        clock=clock,
    )
    await observation.complete_focus_session.execute(
        session,
        account_id=account_id,
        focus_session_id=focus.id,
        completion=FocusSessionCompletion(
            ended_at=kst(2, 0, 40), work_seconds=60, label_id=label_id, description=""
        ),
    )
    after_session = versions(await marked(session, account_id))
    # Only the session start date; 11-02 is not a segment change.
    assert set(after_session) == {nov(1), nov(2)}
    assert after_session[nov(2)] == before_session[nov(2)]

    timeline = create_observation_timeline()
    (segment,) = [
        segment
        for segment in await timeline.read_range(
            session, account_id, start=kst(2, 0, 10), end=kst(2, 0, 11), now=NOW
        )
        if segment.context != OPAQUE
    ]
    await session.commit()
    assert segment.version is not None
    labeling = Labeling(
        accounts=create_account_directory(),
        labels=create_label_catalog(),
        timeline=timeline,
        dispatcher=dispatcher,
        clock=clock,
    )
    await labeling.confirm_segment_label.execute(
        session,
        account_id=account_id,
        selection=LabelSelection(
            segment_id=segment.id, segment_version=segment.version, label_id=label_id
        ),
    )
    after_label = versions(markers := await marked(session, account_id))

    assert set(after_label) == {nov(1), nov(2)}
    assert after_label[nov(1)] > after_session[nov(1)]
    assert after_label[nov(2)] > after_session[nov(2)]
    assert_fresh_source_marks(markers)


@pytest.mark.asyncio
async def test_timeline_change_marks_the_end_date_of_the_activity_before_it(
    integration_session: AsyncSession,
) -> None:
    session = integration_session
    account_id, device_id, _ = await seoul_account(session)
    dispatcher = dispatcher_of("api")
    record = RecordActivity(
        devices=create_device_directory(), dispatcher=dispatcher, clock=FakeClock(NOW)
    )
    # A closed activity ending 11-03 23:55 (Seoul).
    for sequence, observed_at, context in [
        (1, kst(3, 23, 50), detailed("com.example.a")),
        (2, kst(3, 23, 55), OPAQUE),
    ]:
        await record.execute(
            session,
            account_id=account_id,
            record=activity(device_id, sequence, observed_at, context),
        )
    before = versions(await marked(session, account_id))
    assert set(before) == {nov(3)}

    # A change on 11-05 only: its range does not reach 11-03.
    async with unit_of_work(session, dispatcher):
        record_event(
            session,
            TimelineChanged(
                account_id=account_id,
                changed_from=kst(5, 10),
                changed_to=kst(5, 11),
            ),
        )
    after = versions(markers := await marked(session, account_id))

    assert set(after) == {nov(3), nov(5)}
    assert after[nov(3)] > before[nov(3)]
    assert_fresh_source_marks(markers)


@asynccontextmanager
async def reused(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield session


@pytest.mark.asyncio
async def test_consumer_batch_and_replay_mark_the_changed_and_preceding_dates(
    integration_session: AsyncSession,
) -> None:
    session = integration_session
    account_id, device_id, _ = await seoul_account(session)
    other_device = Device(account_id=account_id, idempotency_key=uuid4())
    session.add(other_device)
    await session.commit()
    # As `run_consumer` assembles it, with this test's dispatcher and clock.
    executor = consumer_root.BatchProcessingExecutor(
        lambda: reused(session),
        process_batch=create_process_activity_batch(
            devices=create_device_directory(),
            dispatcher=dispatcher_of("consumer"),
            clock=FakeClock(NOW),
        ),
    )

    async def deliver(device: UUID, records: list[dict[str, object]]) -> None:
        request = ActivityBatchRequest.model_validate(
            {"batchId": str(uuid4()), "deviceId": str(device), "records": records}
        )
        message = ActivityRegistrationRequested.from_batch(account_id, request)
        await executor.execute(message)

    # A segment ending at 11-03 23:55 (Seoul).
    await deliver(
        device_id,
        [
            activity_json(1, kst(3, 23, 50), detailed("com.example.a")),
            activity_json(2, kst(3, 23, 55), detailed("com.example.a")),
        ],
    )
    first = await marked(session, account_id)
    assert set(first) == {nov(3)}

    # The next segment starts on 11-05: 11-03's silence count changes too.
    await deliver(device_id, [activity_json(3, kst(5, 10), detailed("com.example.b"))])
    second = await marked(session, account_id)
    assert set(second) == {nov(3), nov(4), nov(5)}
    assert second[nov(3)]["version"] > first[nov(3)]["version"]

    # A failed attempt is forgotten when a new change marks the date again.
    await session.execute(
        update(analytics_daily_markers)
        .where(analytics_daily_markers.c.account_id == account_id)
        .values(attempts=3, last_error="TimeoutError", last_failed_at=NOW)
    )
    await session.commit()
    # A replay from another device's earlier observation on 11-04.
    await deliver(
        other_device.id, [activity_json(1, kst(4, 21), detailed("com.example.c"))]
    )
    third = await marked(session, account_id)
    replayed = {day for day in third if third[day]["version"] > second[day]["version"]}
    # The replayed range starts at 11-03 23:55 and ends at the 11-04 record.
    assert replayed == {nov(3), nov(4)}
    assert_fresh_source_marks({day: third[day] for day in replayed})
    assert (third[nov(5)]["attempts"], third[nov(5)]["last_error"]) == (
        3,
        "TimeoutError",
    )
