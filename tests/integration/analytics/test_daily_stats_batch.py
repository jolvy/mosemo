"""The daily stats batch on real PostgreSQL connections (verification plan
「PG 두 연결 시나리오」 TX-1~TX-9 and TX-12; TX-13 is `test_protocol_contrasts.py`).

The batch steps are held at the test wait points. Each scenario ends with I1
(a read equals the source computed then) and I2 (after one more batch alone:
no marker and the stored row equals the source). Versions are compared by
size because `nextval` is not rolled back. Source writes happen after the
batch run started (`clock.advance`), as they would in production.
"""

import asyncio
from datetime import timedelta
from time import perf_counter

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from mosemo.analytics.application import daily_stats_batch, reads
from mosemo.analytics.application.daily_stats_batch import (
    AFTER_CLAIM,
    AFTER_SOURCE_READ,
    BEFORE_SAVE_COMMIT,
)
from mosemo.analytics.application.source_loader import SourceSnapshotLoader
from mosemo.analytics.bootstrap import public_suffix_rules
from mosemo.analytics.domain.markers import RETRY_POLICY, MarkerKind
from mosemo.analytics.domain.stored_stats import StoredDailyStats
from mosemo.analytics.domain.usage_targets import UsageTargetResolver
from mosemo.analytics.infrastructure.daily_stats import DailyStatsRepository
from mosemo.labeling.bootstrap import create_label_confirmation_directory
from mosemo.observation.bootstrap import (
    create_focus_session_log,
    create_observation_timeline,
)
from mosemo.observation.domain.timeline import ActivityTimelineSegment
from tests.support.analytics_batch import (
    DAY,
    EDITOR,
    BatchWorld,
    background,
    definition,
    later_activity,
)
from tests.support.analytics_sources import SourceSeed

pytestmark = pytest.mark.asyncio

MINUTE = timedelta(minutes=1)
DEFINITION_1 = definition(1)
DEFINITION_2 = definition(2)


EDITORS: dict[object, ActivityTimelineSegment] = {}


async def seeded(world: BatchWorld) -> SourceSeed:
    """An account with one closed activity on 11-03 and a marker for 11-03."""
    seed = await world.account()
    async with world.seeding(seed):
        EDITORS[seed.account_id] = seed.activity(540, 570, EDITOR)
    await world.mark_days(seed)
    return seed


async def assert_read_is_current(world: BatchWorld, seed: SourceSeed) -> None:
    """I1: a read returns the source computed now."""
    view = await world.read(seed.account_id)
    assert view.stats == await world.computed(seed.account_id)


async def assert_converged(
    world: BatchWorld, seed: SourceSeed, *, version: int = 1
) -> None:
    """I2: one more batch alone leaves no marker and a current stored row."""
    world.clock.advance(MINUTE)
    await world.make_due()
    await world.run(world.refresh(definition=definition(version)))
    assert await world.marker_rows(seed.account_id) == []
    stored = await world.stored(seed.account_id)
    assert stored is not None
    assert stored.stats == await world.computed(seed.account_id)
    assert stored.definition_version == version
    await assert_read_is_current(world, seed)


async def marker_version(world: BatchWorld, seed: SourceSeed) -> int:
    marker = await world.marker(seed.account_id)
    assert marker is not None
    return marker["version"]


async def test_tx1_source_change_during_the_calculation_leaves_the_new_marker(
    world: BatchWorld,
) -> None:
    """TX-1 (T1): the source changes (v2) after the batch read it; the batch
    saves v1 but its DELETE finds 0 rows, v2 stays and reads see the change."""
    seed = await seeded(world)
    v1 = await marker_version(world, seed)

    async with background(world.run(world.refresh(wait=[AFTER_SOURCE_READ]))) as run:
        await world.gate.reached(AFTER_SOURCE_READ)
        world.clock.advance(MINUTE)
        v2 = await world.write_source(seed, change=later_activity(600))
        world.gate.release(AFTER_SOURCE_READ)
        report = await run

    assert (report.saved, report.superseded, report.stale) == (0, 1, 0)
    assert v2 > v1
    marker = await world.marker(seed.account_id)
    assert marker is not None
    assert (marker["version"], marker["attempts"]) == (v2, 0)
    stored = await world.stored(seed.account_id)
    assert stored is not None
    assert stored.source_version == v1
    assert stored.stats != await world.computed(seed.account_id)
    await assert_read_is_current(world, seed)
    await assert_converged(world, seed)


async def test_tx2_a_late_older_batch_does_not_overwrite_a_newer_save(
    world: BatchWorld,
) -> None:
    """TX-2 (T2): batch 1 read the old source; meanwhile batch 2 saved the new
    version. Batch 1's UPSERT writes 0 rows (guard) and the row stays."""
    seed = await seeded(world)
    v1 = await marker_version(world, seed)

    async with background(world.run(world.refresh(wait=[AFTER_SOURCE_READ]))) as run:
        await world.gate.reached(AFTER_SOURCE_READ)
        world.clock.advance(MINUTE)
        v2 = await world.write_source(seed, change=later_activity(600))
        second = await world.run()
        newer = await world.stored(seed.account_id)
        world.gate.release(AFTER_SOURCE_READ)
        first = await run

    assert second.saved == 1
    assert (first.saved, first.superseded, first.stale) == (0, 0, 1)
    assert v2 > v1
    stored = await world.stored(seed.account_id)
    assert newer is not None
    assert stored is not None
    assert stored.source_version == newer.source_version == v2
    assert stored.stats == await world.computed(seed.account_id)
    await assert_read_is_current(world, seed)
    await assert_converged(world, seed)


async def test_tx3_the_conditional_delete_waits_and_rechecks_the_version(
    world: BatchWorld,
) -> None:
    """TX-3 (T3): a source write upserted its marker but has not committed;
    the batch DELETE waits for the row lock and, once it is released, deletes
    0 rows. The new marker stays."""
    seed = await seeded(world)
    v1 = await marker_version(world, seed)
    written, hold = asyncio.Event(), asyncio.Event()

    async with background(world.run(world.refresh(wait=[AFTER_SOURCE_READ]))) as run:
        await world.gate.reached(AFTER_SOURCE_READ)
        world.clock.advance(MINUTE)
        async with background(
            world.write_source(
                seed, change=later_activity(600), written=written, hold=hold
            )
        ) as write:
            await written.wait()
            world.gate.release(AFTER_SOURCE_READ)
            await world.wait_for_lock_waiter()
            hold.set()
            v2 = await write
        report = await run

    assert (report.saved, report.superseded, report.failures.total()) == (0, 1, 0)
    assert v2 > v1
    assert await marker_version(world, seed) == v2
    await assert_read_is_current(world, seed)
    await assert_converged(world, seed)


class FailingSave(DailyStatsRepository):
    async def save_if_newer(self, db_session, stored: StoredDailyStats) -> bool:
        raise RuntimeError("save failed")


async def test_tx4_a_failing_save_keeps_the_correction_visible(
    world: BatchWorld,
) -> None:
    """TX-4 (T4): after a label correction (marker v2) the batch save raises.
    The correction committed, reads reflect it, the next batch saves it."""
    seed = await seeded(world)
    await world.run()
    world.clock.advance(MINUTE)
    editor = EDITORS[seed.account_id]
    v2 = await world.write_source(seed, change=lambda s: s.confirm(editor, s.coding))

    with pytest.raises(RuntimeError, match="save failed"):
        await world.run(world.refresh(stored_stats=FailingSave()))

    marker = await world.marker(seed.account_id)
    assert marker is not None
    assert (marker["version"], marker["attempts"]) == (v2, 1)
    stored = await world.stored(seed.account_id)
    assert stored is not None
    assert stored.stats != await world.computed(seed.account_id)
    view = await world.read(seed.account_id)
    # The confirmed segment is no longer pending.
    assert (view.stats.auxiliary.pending_us, stored.stats.auxiliary.pending_us) == (
        0,
        30 * 60_000_000,
    )
    await assert_read_is_current(world, seed)
    await assert_converged(world, seed)


async def test_tx5_the_batch_never_blocks_a_source_write(world: BatchWorld) -> None:
    """TX-5 (T5): while the batch computes, a source write with
    `lock_timeout = 3s` commits at once. Contrast: a held FOR UPDATE on the
    marker makes the same write fail with 55P03."""
    seed = await seeded(world)

    async with background(world.run(world.refresh(wait=[AFTER_SOURCE_READ]))) as run:
        await world.gate.reached(AFTER_SOURCE_READ)
        world.clock.advance(MINUTE)
        started = perf_counter()
        await world.write_source(seed, change=later_activity(600))
        assert perf_counter() - started < 1
        assert await world.lock_waiters() == 0
        world.gate.release(AFTER_SOURCE_READ)
        await run

    async with world.engine.connect() as holder:
        await holder.execute(
            text(
                "SELECT 1 FROM analytics_daily_markers WHERE account_id = :id FOR UPDATE"
            ),
            {"id": seed.account_id},
        )
        with pytest.raises(DBAPIError) as failure:
            await world.write_source(seed, change=later_activity(620))
        await holder.rollback()
    assert getattr(failure.value.orig, "sqlstate", None) == "55P03"
    await assert_read_is_current(world, seed)
    await assert_converged(world, seed)


async def test_tx6_first_save_reads_equal_the_source_before_during_and_after(
    world: BatchWorld,
) -> None:
    """TX-6 (T6): no stored row and marker v1; reads before, during and after
    the batch equal the source; the end has a stored row and no marker."""
    seed = await seeded(world)
    await assert_read_is_current(world, seed)

    async with background(world.run(world.refresh(wait=[BEFORE_SAVE_COMMIT]))) as run:
        await world.gate.reached(BEFORE_SAVE_COMMIT)
        await assert_read_is_current(world, seed)
        world.gate.release(BEFORE_SAVE_COMMIT)
        report = await run

    assert (report.saved, report.superseded, report.stale) == (1, 0, 0)
    assert await world.marker_rows(seed.account_id) == []
    stored = await world.stored(seed.account_id)
    assert stored is not None
    assert stored.stats == await world.computed(seed.account_id)
    await assert_read_is_current(world, seed)


async def test_tx7_an_old_definition_batch_rolls_back_after_the_definition_rises(
    world: BatchWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TX-7 (T7): a definition 1 batch read the source; then the definition row
    goes 1→2 and the marks run. Its save reads 2 under FOR SHARE and rolls
    back; the next definition 2 batch saves."""
    seed = await seeded(world)
    await world.save_definition_row(seed, definition_version=1, source_version=1)
    v1 = await marker_version(world, seed)

    refresh = world.refresh(definition=DEFINITION_1, wait=[AFTER_SOURCE_READ])
    async with background(world.run(refresh)) as run:
        await world.gate.reached(AFTER_SOURCE_READ)
        world.clock.advance(MINUTE)
        await world.set_definition_row(2)
        marked = await world.mark(definition=DEFINITION_2)
        world.gate.release(AFTER_SOURCE_READ)
        report = await run

    assert (marked.accounts, marked.markers) == (1, 1)
    assert (report.aborted, report.saved, report.stale) == ("definition", 0, 0)
    marker = await world.marker(seed.account_id)
    assert marker is not None
    assert marker["kind"] == MarkerKind.DEFINITION
    assert marker["version"] > v1
    stored = await world.stored(seed.account_id)
    assert stored is not None
    assert stored.definition_version == 1
    monkeypatch.setattr(reads, "CURRENT_DEFINITION", DEFINITION_2)
    await assert_read_is_current(world, seed)
    await assert_converged(world, seed, version=2)


async def test_tx8_a_definition_update_waits_for_the_save_holding_for_share(
    world: BatchWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TX-8 (T8): the definition UPDATE waits until the save transaction that
    holds the definition row FOR SHARE commits; reprocessing then stores 2."""
    seed = await seeded(world)

    async def raise_definition() -> None:
        async with world.engine.begin() as connection:
            await connection.execute(
                text("UPDATE analytics_definitions SET version = 2")
            )

    refresh = world.refresh(definition=DEFINITION_1, wait=[BEFORE_SAVE_COMMIT])
    async with background(world.run(refresh)) as run:
        await world.gate.reached(BEFORE_SAVE_COMMIT)
        async with background(raise_definition()) as update:
            await world.wait_for_lock_waiter()
            assert not update.done()
            world.gate.release(BEFORE_SAVE_COMMIT)
            report = await run
            await update

    assert report.saved == 1
    stored = await world.stored(seed.account_id)
    assert stored is not None
    assert stored.definition_version == 1
    world.clock.advance(MINUTE)
    assert (await world.mark(definition=DEFINITION_2)).markers == 1
    monkeypatch.setattr(reads, "CURRENT_DEFINITION", DEFINITION_2)
    await assert_converged(world, seed, version=2)


async def test_tx9_definition_marks_wait_for_the_account_lock_of_a_source_write(
    world: BatchWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TX-9 (T9): a source write drew its marker version and has not committed;
    the definition mark waits for the account lock and then writes a larger
    version."""
    seed = await seeded(world)
    await world.run()
    await world.set_definition_row(2)
    written, hold = asyncio.Event(), asyncio.Event()

    world.clock.advance(MINUTE)
    async with background(
        world.write_source(seed, change=later_activity(600), written=written, hold=hold)
    ) as write:
        await written.wait()
        async with background(world.mark(definition=DEFINITION_2)) as mark:
            await world.wait_for_lock_waiter()
            assert not mark.done()
            hold.set()
            source_version = await write
            marked = await mark

    assert marked.markers == 1
    marker = await world.marker(seed.account_id)
    assert marker is not None
    assert marker["kind"] == MarkerKind.DEFINITION
    assert marker["version"] > source_version
    monkeypatch.setattr(reads, "CURRENT_DEFINITION", DEFINITION_2)
    await assert_read_is_current(world, seed)
    await assert_converged(world, seed, version=2)


class PausingLoader(SourceSnapshotLoader):
    """Pauses between the segment reads and the confirmation read."""

    def __init__(self, paused: asyncio.Event, resume: asyncio.Event) -> None:
        super().__init__(
            timeline=create_observation_timeline(),
            focus_sessions=create_focus_session_log(),
            confirmations=create_label_confirmation_directory(),
            resolver=UsageTargetResolver(public_suffix_rules()),
        )
        self.paused = paused
        self.resume = resume

    async def _read_confirmations(self, db_session, account_id, *, rows):
        self.paused.set()
        await self.resume.wait()
        return await super()._read_confirmations(db_session, account_id, rows=rows)


async def test_tx12_a_torn_read_may_be_stored_but_is_never_used(
    world: BatchWorld,
) -> None:
    """TX-12 (T12): two source commits land between the batch's source reads.
    The torn value is stored, but the markers remain, so reads compute from
    the source and the next batch fixes the row."""
    seed = await seeded(world)
    paused, resume = asyncio.Event(), asyncio.Event()
    editor = EDITORS[seed.account_id]

    refresh = world.refresh(loader=PausingLoader(paused, resume))
    async with background(world.run(refresh)) as run:
        await paused.wait()
        world.clock.advance(MINUTE)
        await world.write_source(seed, change=lambda s: s.confirm(editor, s.coding))
        await world.write_source(seed, change=later_activity(600))
        resume.set()
        report = await run

    assert report.superseded == 1
    stored = await world.stored(seed.account_id)
    assert stored is not None
    torn = stored.stats
    assert torn != await world.computed(seed.account_id)
    # New confirmations over the old segments: a source that never existed.
    assert (torn.auxiliary.pending_us, torn.usage.observed_us) == (0, 30 * 60_000_000)
    assert await world.marker(seed.account_id) is not None
    await assert_read_is_current(world, seed)
    await assert_converged(world, seed)


async def test_dates_left_when_the_budget_runs_out_keep_their_attempts(
    world: BatchWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 120 s budget is checked between dates. Claimed dates the run did
    not reach give back their attempt and are due again, so repeated budget
    overruns never quarantine a healthy date."""
    seed = await seeded(world)
    await world.mark_days(seed, DAY + timedelta(days=1))
    monkeypatch.setattr(daily_stats_batch, "RUN_BUDGET_SECONDS", 0.2)

    for _ in range(RETRY_POLICY.quarantine_threshold + 1):
        async with background(world.run(world.refresh(wait=[AFTER_CLAIM]))) as run:
            await world.gate.reached(AFTER_CLAIM)
            await asyncio.sleep(0.3)
            world.gate.release(AFTER_CLAIM)
            report = await run
        assert (report.released, report.saved, report.failures.total()) == (2, 0, 0)
        rows = await world.marker_rows(seed.account_id)
        assert [row["attempts"] for row in rows] == [0, 0]
        assert all(row["next_attempt_at"] == world.clock() for row in rows)
        assert all(row["last_error"] is None for row in rows)

    monkeypatch.setattr(daily_stats_batch, "RUN_BUDGET_SECONDS", 120.0)
    assert (await world.run()).saved == 2
    assert await world.marker_rows(seed.account_id) == []
