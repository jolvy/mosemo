"""Account timezone change and the daily stats (AC-8028, AC-8029; ADR 0016
「변경 표시」 시간대 변경, 「표시와 배치」 시간대 변경).

`ChangeAccountTimezone` runs with the analytics handlers registered, as the
assembly roots do. Seoul 11-01 08:00 is New York 10-31 19:00, so a timezone
change moves that activity to another date.
"""

import asyncio
from datetime import date, timedelta
from uuid import UUID

import pytest
from sqlalchemy import update

from mosemo.analytics.application.daily_stats_batch import (
    AFTER_SOURCE_READ,
    BEFORE_SAVE_COMMIT,
)
from mosemo.analytics.bootstrap import register_handlers
from mosemo.analytics.domain.markers import MarkerKind, MarkScope
from mosemo.analytics.infrastructure.tables import analytics_daily_markers
from mosemo.identity.application.accounts import ChangeAccountTimezone
from mosemo.identity.bootstrap import create_account_directory
from mosemo.identity.infrastructure.accounts import AccountRepository
from mosemo.identity.public import AccountTimezoneChanged
from mosemo.labeling.application.confirmations import LabelSelection
from mosemo.labeling.bootstrap import Labeling
from mosemo.labeling.public import LabelConfirmed
from mosemo.labels.bootstrap import create_label_catalog
from mosemo.observation.bootstrap import (
    create_focus_session_log,
    create_observation_timeline,
)
from mosemo.observation.domain.timeline import ActivityTimelineSegment
from mosemo.platform.database.transactions import read_snapshot
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.domain.events import DomainEvent
from mosemo.shared.domain.timezones import Timezone
from tests.support.analytics_batch import (
    EDITOR,
    NOTES,
    SEOUL,
    BatchWorld,
    background,
    day_minutes,
)
from tests.support.analytics_sources import SourceSeed

pytestmark = pytest.mark.asyncio

NEW_YORK = Timezone.AMERICA_NEW_YORK
MINUTE = timedelta(minutes=1)
OCT_31 = date(2026, 10, 31)
NOV_1 = date(2026, 11, 1)
NOV_2 = date(2026, 11, 2)


def nov(day: int) -> date:
    return date(2026, 11, day)


def seoul(day: date, hour: int, minute: int = 0) -> float:
    """Minutes of `day` `hour`:`minute` Seoul in `tests.support.analytics_sources`."""
    return day_minutes(day) + hour * 60 + minute


async def seeded(world: BatchWorld) -> tuple[SourceSeed, ActivityTimelineSegment]:
    """A Seoul account: an activity at Seoul 11-01 08:00 (New York 10-31 19:00)
    and one at Seoul 11-02 01:00 (New York 11-01 11:00); none on Seoul 10-31."""
    seed = await world.account()
    async with world.seeding(seed):
        editor = seed.activity(seoul(NOV_1, 8), seoul(NOV_1, 8, 30), EDITOR)
        seed.activity(seoul(NOV_2, 1), seoul(NOV_2, 1, 20), NOTES)
    return seed, editor


def change_use_case(dispatcher: EventDispatcher) -> ChangeAccountTimezone:
    return ChangeAccountTimezone(accounts=AccountRepository(), dispatcher=dispatcher)


async def change_timezone(
    world: BatchWorld,
    seed: SourceSeed,
    timezone: Timezone = NEW_YORK,
    *,
    dispatcher: EventDispatcher | None = None,
) -> None:
    async with world.factory() as session:
        await change_use_case(dispatcher or world.dispatcher).execute(
            session, account_id=seed.account_id, timezone=timezone
        )


async def confirm(
    world: BatchWorld,
    seed: SourceSeed,
    segment: ActivityTimelineSegment,
    *,
    dispatcher: EventDispatcher | None = None,
) -> None:
    """A real label confirmation (account lock, then `LabelConfirmed`)."""
    timeline = create_observation_timeline()
    async with world.factory() as session:
        async with read_snapshot(session):
            view = await timeline.find_segment(
                session,
                account_id=seed.account_id,
                segment_id=segment.id,
                now=world.clock(),
            )
        assert view is not None
        assert view.version is not None
        labeling = Labeling(
            accounts=create_account_directory(),
            labels=create_label_catalog(),
            timeline=timeline,
            dispatcher=dispatcher or world.dispatcher,
            clock=world.clock,
        )
        await labeling.confirm_segment_label.execute(
            session,
            account_id=seed.account_id,
            selection=LabelSelection(
                segment_id=view.id, segment_version=view.version, label_id=seed.coding
            ),
        )


class Hold:
    """A dispatcher with the analytics handlers and, after them, a handler
    that keeps the transaction open (with the account lock) until released."""

    def __init__(self, world: BatchWorld, event_type: type[DomainEvent]) -> None:
        self.entered = asyncio.Event()
        self.released = asyncio.Event()
        self.dispatcher = EventDispatcher()
        register_handlers(
            self.dispatcher,
            timeline=create_observation_timeline(),
            accounts=create_account_directory(),
            focus_sessions=create_focus_session_log(),
            clock=world.clock,
        )
        self.dispatcher.subscribe(event_type, self.hold)

    async def hold(self, event: DomainEvent, db_session) -> None:
        self.entered.set()
        await self.released.wait()


async def run_until_unmarked(world: BatchWorld, account_id: UUID) -> None:
    for _ in range(3):
        world.clock.advance(MINUTE)
        await world.make_due()
        await world.run()
        if not await world.marker_rows(account_id):
            return
    raise AssertionError("markers left after three runs")


async def assert_new_york_reads(world: BatchWorld, account_id: UUID) -> None:
    for day in (OCT_31, NOV_1):
        view = await world.read(account_id, day)
        computed = await world.computed(account_id, day, timezone=NEW_YORK)
        assert view.stats == computed, day


async def assert_new_york_rows(
    world: BatchWorld, account_id: UUID, *days: date
) -> None:
    for day in days:
        stored = await world.stored(account_id, day)
        assert stored is not None, day
        assert stored.timezone == NEW_YORK, day
        assert stored.stats == await world.computed(
            account_id, day, timezone=NEW_YORK
        ), day


async def assert_marked_in_new_york(world: BatchWorld, account_id: UUID) -> None:
    rows = await world.marker_rows(account_id)
    assert rows
    for row in rows:
        assert row["eligible_at"] == MarkScope.eligible_at(
            row["local_date"], NEW_YORK
        ), row["local_date"]


async def test_ac8028_past_dates_follow_the_new_timezone_boundaries(
    world: BatchWorld,
) -> None:
    """AC-8028: stored Seoul dates, a change to New York, then batches until
    no marker is left. Reads match New York from the change on."""
    seed, _ = await seeded(world)
    account_id = seed.account_id
    await world.mark_days(seed, OCT_31, NOV_1, NOV_2)
    assert (await world.run()).saved == 3
    seoul_oct_31 = await world.stored(account_id, OCT_31)
    assert seoul_oct_31 is not None
    assert seoul_oct_31.timezone == SEOUL
    new_york_oct_31 = await world.computed(account_id, OCT_31, timezone=NEW_YORK)
    # The Seoul 11-01 08:00 activity falls on New York 10-31.
    assert new_york_oct_31.usage.observed_us > 0
    assert seoul_oct_31.stats.usage.observed_us == 0

    world.clock.advance(MINUTE)
    await change_timezone(world, seed)

    await assert_new_york_reads(world, account_id)
    assert {row["local_date"] for row in await world.marker_rows(account_id)} == {
        OCT_31 - timedelta(days=1),
        OCT_31,
        NOV_1,
        NOV_2,
        nov(3),
    }
    await assert_marked_in_new_york(world, account_id)

    # Reads during the batch, before each date's save commits.
    world.clock.advance(MINUTE)
    await world.make_due()
    async with background(world.run(world.refresh(wait=[BEFORE_SAVE_COMMIT]))) as run:
        for _ in range(5):
            await world.gate.reached(BEFORE_SAVE_COMMIT)
            await assert_new_york_reads(world, account_id)
            world.gate.release(BEFORE_SAVE_COMMIT)
        report = await run
    assert report.saved == 5

    await run_until_unmarked(world, account_id)
    assert await world.marker_rows(account_id) == []
    await assert_new_york_rows(world, account_id, OCT_31, NOV_1)
    await assert_new_york_reads(world, account_id)


async def test_ac8029_a_seoul_save_rolls_back_after_the_change(
    world: BatchWorld,
) -> None:
    """AC-8029: the batch read a date in Seoul (`after_source_read`); the
    timezone changes to New York; its save rolls back and the next run saves
    the date in New York."""
    seed, _ = await seeded(world)
    account_id = seed.account_id
    await world.mark_days(seed, NOV_1)

    async with background(world.run(world.refresh(wait=[AFTER_SOURCE_READ]))) as run:
        await world.gate.reached(AFTER_SOURCE_READ)
        world.clock.advance(MINUTE)
        await change_timezone(world, seed)
        world.gate.release(AFTER_SOURCE_READ)
        report = await run

    assert (report.saved, report.superseded, report.stale) == (0, 0, 0)
    assert not report.failed
    assert await world.stored(account_id, NOV_1) is None
    marker = await world.existing_marker(account_id, NOV_1)
    assert marker["attempts"] == 0
    await assert_marked_in_new_york(world, account_id)

    await run_until_unmarked(world, account_id)
    await assert_new_york_rows(world, account_id, OCT_31, NOV_1, NOV_2)
    await assert_new_york_reads(world, account_id)


async def test_marker_only_dates_and_their_neighbours_are_marked(
    world: BatchWorld,
) -> None:
    """Contrast: 11-04 and 11-06 neighbour only a marked date (11-05) with no
    stored row; marking the stored dates alone would miss them."""
    seed, _ = await seeded(world)
    account_id = seed.account_id
    await world.mark_days(seed, NOV_1)
    assert (await world.run()).saved == 1
    await world.mark_days(seed, nov(5))
    async with world.engine.begin() as connection:
        # A marker in retry as a definition marker: the change resets it.
        await connection.execute(
            update(analytics_daily_markers)
            .where(analytics_daily_markers.c.account_id == account_id)
            .values(kind=MarkerKind.DEFINITION, attempts=2)
        )
    stored_only = MarkScope.dates_for_timezone_change({NOV_1})
    assert {nov(4), nov(6)}.isdisjoint(stored_only)

    world.clock.advance(MINUTE)
    await change_timezone(world, seed)

    rows = await world.marker_rows(account_id)
    assert [row["local_date"] for row in rows] == [
        OCT_31,
        NOV_1,
        NOV_2,
        nov(4),
        nov(5),
        nov(6),
    ]
    for row in rows:
        assert row["kind"] == MarkerKind.SOURCE
        assert row["attempts"] == 0
        assert row["marked_at"] == world.clock()
    await assert_marked_in_new_york(world, account_id)


async def test_change_waits_for_a_label_confirmation_holding_the_lock(
    world: BatchWorld,
) -> None:
    """AC-8029 and the lock order: a confirmation holds the account lock (it
    marked in Seoul); the change waits, then marks in New York. Both succeed
    and the confirmation counts in the New York results."""
    seed, editor = await seeded(world)
    account_id = seed.account_id
    before = await world.computed(account_id, OCT_31, timezone=NEW_YORK)
    hold = Hold(world, LabelConfirmed)

    async with background(confirm(world, seed, editor, dispatcher=hold.dispatcher)) as (
        confirming
    ):
        await hold.entered.wait()
        async with background(change_timezone(world, seed)) as changing:
            await world.wait_for_lock_waiter()
            assert not changing.done()
            hold.released.set()
            await confirming
            await changing

    await assert_marked_in_new_york(world, account_id)
    await run_until_unmarked(world, account_id)
    await assert_new_york_rows(world, account_id, OCT_31, NOV_1)
    stored = await world.stored(account_id, OCT_31)
    assert stored is not None
    assert stored.stats != before
    await assert_new_york_reads(world, account_id)


async def test_label_confirmation_waits_for_a_change_holding_the_lock(
    world: BatchWorld,
) -> None:
    """The reverse order: the change holds the account lock after its marks;
    the confirmation waits, then reads New York and marks its New York date."""
    seed, editor = await seeded(world)
    account_id = seed.account_id
    before = await world.computed(account_id, OCT_31, timezone=NEW_YORK)
    hold = Hold(world, AccountTimezoneChanged)

    async with background(
        change_timezone(world, seed, dispatcher=hold.dispatcher)
    ) as changing:
        await hold.entered.wait()
        async with background(confirm(world, seed, editor)) as confirming:
            await world.wait_for_lock_waiter()
            assert not confirming.done()
            hold.released.set()
            await changing
            await confirming

    # The editor activity is New York 10-31: the confirmation marked it.
    assert [row["local_date"] for row in await world.marker_rows(account_id)] == [
        OCT_31
    ]
    await assert_marked_in_new_york(world, account_id)
    await run_until_unmarked(world, account_id)
    await assert_new_york_rows(world, account_id, OCT_31)
    stored = await world.stored(account_id, OCT_31)
    assert stored is not None
    assert stored.stats != before
    await assert_new_york_reads(world, account_id)
