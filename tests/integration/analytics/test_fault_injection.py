"""Fault injection inside the test process (verification plan 「장애 주입」
FI-1, FI-6~FI-12, FI-14, FI-16). Process kills are in
`test_fault_injection_processes.py`.

Backoffs and leases are not waited out: the operator connection pulls
`next_attempt_at` back (`make_due`).
"""

import asyncio
import logging
import socket
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from testcontainers.community.postgres import PostgresContainer

from mosemo.analytics.application import daily_stats_batch, reads
from mosemo.analytics.application.daily_stats_batch import (
    AFTER_CLAIM,
    BEFORE_SAVE_COMMIT,
)
from mosemo.analytics.application.reads import GetDailyStats, StoredStatsLookup
from mosemo.analytics.bootstrap import AnalyticsBatchConfig, create_daily_stats_job
from mosemo.analytics.domain.markers import RETRY_POLICY, MarkerKind
from mosemo.analytics.infrastructure.daily_stats import DailyStatsRepository
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository
from mosemo.identity.bootstrap import create_account_directory
from mosemo.observation.public import ActivityTimelineBusyError
from mosemo.platform.clock import utc_now
from mosemo.platform.scheduling import execute_job
from tests.support.analytics_batch import (
    DAY,
    EDITOR,
    BatchWorld,
    FailingCalculator,
    background,
    create_database,
    definition,
    later_activity,
)
from tests.support.analytics_sources import SourceSeed

pytestmark = [pytest.mark.fault_injection, pytest.mark.asyncio]

NEXT_DAY = DAY + timedelta(days=1)
MINUTE = timedelta(minutes=1)
DEFINITION_1 = definition(1)
DEFINITION_2 = definition(2)
RELEASE_QUARANTINE = (
    "UPDATE analytics_daily_markers SET attempts = 0, next_attempt_at = now(),"
    " last_error = NULL, last_failed_at = NULL WHERE attempts >= 6"
)


async def seeded(world: BatchWorld, *days) -> SourceSeed:
    """An account with an activity on each day, marked in the given order."""
    seed = await world.account()
    async with world.seeding(seed):
        for day in days or (DAY,):
            start = (day - DAY).days * 1440 + 540
            seed.activity(start, start + 30, EDITOR)
    await world.mark_days(seed, *(days or (DAY,)))
    return seed


async def assert_current(world: BatchWorld, seed: SourceSeed, *days) -> None:
    """I2 on each day: no marker, stored row = source, read = source."""
    assert await world.marker_rows(seed.account_id) == []
    for day in days or (DAY,):
        stored = await world.stored(seed.account_id, day)
        assert stored is not None
        computed = await world.computed(seed.account_id, day)
        assert stored.stats == computed
        assert (await world.read(seed.account_id, day)).stats == computed


async def quarantine(world: BatchWorld) -> SourceSeed:
    """FI-1: DAY fails in every attempt; NEXT_DAY is saved on the first run."""
    seed = await seeded(world, NEXT_DAY, DAY)
    failing = world.refresh(calculator=FailingCalculator(fail_on=[DAY]))
    reports = []
    for _ in range(RETRY_POLICY.quarantine_threshold):
        reports.append(await world.run(failing))
        await world.make_due()
    assert reports[0].saved == 1
    assert [report.failures["compute"] for report in reports] == [1] * 6
    assert [report.quarantined_new for report in reports] == [0] * 5 + [1]
    return seed


async def test_fi1_calculation_failure_is_retried_then_quarantined(
    world: BatchWorld,
) -> None:
    """FI-1: two marked dates, the calculation of one always raises, six runs.
    The other date is saved on the first run; the failing date leaves the
    claim at attempt 6, and its read fails with the calculation error."""
    seed = await quarantine(world)

    marker = await world.marker(seed.account_id, DAY)
    assert marker is not None
    assert (marker["attempts"], marker["last_error"]) == (6, "ArithmeticError")
    assert await world.marker(seed.account_id, NEXT_DAY) is None
    assert (await world.run()).saved == 0
    assert (await world.existing_marker(seed.account_id, DAY))["attempts"] == 6
    failing_read = GetDailyStats(
        accounts=create_account_directory(),
        loader=world.analytics.source_loader,
        calculator=FailingCalculator(fail_on=[DAY]),
        stored_stats=StoredStatsLookup(
            markers=RecomputeMarkerRepository(), stored_stats=DailyStatsRepository()
        ),
        clock=world.clock,
    )
    async with world.factory() as session:
        with pytest.raises(ArithmeticError):
            await failing_read.execute(session, account_id=seed.account_id, day=DAY)
    view = await world.read(seed.account_id, DAY)
    assert view.stats == await world.computed(seed.account_id, DAY)


async def test_fi16_released_quarantine_is_saved_on_the_next_run(
    world: BatchWorld,
) -> None:
    """FI-16: from the FI-1 end state, with the failure fixed, the release SQL
    of the operations guide makes the next run save the date. A quarantined
    marker is never dropped without a save."""
    seed = await quarantine(world)
    world.clock.now = utc_now() + MINUTE
    assert (await world.run()).saved == 0
    assert await world.marker(seed.account_id, DAY) is not None

    async with world.engine.begin() as connection:
        released = await connection.execute(text(RELEASE_QUARANTINE))
    assert released.rowcount == 1
    report = await world.run()

    assert (report.saved, report.failures.total()) == (1, 0)
    await assert_current(world, seed, DAY, NEXT_DAY)


async def test_fi6_lost_connection_ends_the_run_and_the_next_tick_saves_all(
    world: BatchWorld,
) -> None:
    """FI-6: three dates claimed, then `pg_terminate_backend` of the batch
    connection. The run fails without trying the other dates, the job
    wrapper survives, and the next tick saves all three."""
    days = [DAY - timedelta(days=2), DAY - timedelta(days=1), DAY]
    seed = await seeded(world, *days)
    config = AnalyticsBatchConfig(
        reprocess_per_run=600,
        test_wait_points=frozenset({BEFORE_SAVE_COMMIT}),
        test_wait_directory=world.gate.directory,
    )
    job = create_daily_stats_job(
        world.factory,
        config=config,
        analytics=world.analytics,
        dispatcher=world.dispatcher,
        clock=world.clock,
    )

    async with background(execute_job(job)) as run:
        await world.gate.reached(BEFORE_SAVE_COMMIT)
        async with world.engine.connect() as operator:
            await operator.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND pid <> pg_backend_pid()"
                    " AND state = 'idle in transaction'"
                )
            )
        world.gate.release(BEFORE_SAVE_COMMIT)
        assert await run is False

    markers = await world.marker_rows(seed.account_id)
    assert [row["attempts"] for row in markers] == [1, 1, 1]
    assert all(row["last_error"] is None for row in markers)
    assert all([await world.stored(seed.account_id, day) is None for day in days])
    await world.make_due()
    report = await world.run()
    assert report.saved == 3
    await assert_current(world, seed, *days)


@pytest.fixture
def restartable_database():
    """A PostgreSQL container of this test alone, on a fixed host port so the
    URL survives a restart (the shared container is never restarted)."""
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    with PostgresContainer("postgres:18", driver="asyncpg").with_bind_ports(
        5432, port
    ) as postgres:
        url = postgres.get_connection_url()
        yield postgres, create_database(url, "analytics_restart")


@pytest_asyncio.fixture
async def restart_world(restartable_database, tmp_path) -> AsyncIterator[tuple]:
    postgres, url = restartable_database
    world = BatchWorld(url, tmp_path)
    try:
        yield postgres, world
    finally:
        await world.close()


async def test_fi7_database_restart_fails_one_run_and_the_job_recovers(
    restart_world, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FI-7: the database stops after the claim and starts again. The run
    logs a failure, the job wrapper survives and the next run saves."""
    postgres, world = restart_world
    seed = await seeded(world)
    config = AnalyticsBatchConfig(
        reprocess_per_run=600,
        test_wait_points=frozenset({AFTER_CLAIM}),
        test_wait_directory=world.gate.directory,
    )
    job = create_daily_stats_job(
        world.factory,
        config=config,
        analytics=world.analytics,
        dispatcher=world.dispatcher,
        clock=world.clock,
    )
    # Alembic's fileConfig in the database fixture disables existing loggers.
    monkeypatch.setattr(
        logging.getLogger("mosemo.platform.scheduling"), "disabled", False
    )
    caplog.set_level(logging.INFO, logger="mosemo.platform.scheduling")
    container = postgres.get_wrapped_container()

    async with background(execute_job(job)) as run:
        await world.gate.reached(AFTER_CLAIM)
        await asyncio.to_thread(container.stop)
        world.gate.release(AFTER_CLAIM)
        assert await run is False
    await asyncio.to_thread(container.start)
    async with asyncio.timeout(60):
        while True:
            try:
                async with world.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                break
            except OSError, DBAPIError:  # the server is still starting
                await asyncio.sleep(0.5)

    failures = [r for r in caplog.records if r.getMessage() == "batch job failed"]
    assert len(failures) == 1
    await world.make_due()
    # The same job without the wait point: the process would just keep going.
    recovered = create_daily_stats_job(
        world.factory,
        config=AnalyticsBatchConfig(reprocess_per_run=600),
        analytics=world.analytics,
        dispatcher=world.dispatcher,
        clock=world.clock,
    )
    assert await execute_job(recovered) is True
    await assert_current(world, seed)


async def test_fi8_save_lock_timeout_fails_only_that_date(world: BatchWorld) -> None:
    """FI-8: a source write holds the marker row; the batch save times out
    (55P03) and rolls back; its failure record finds the new version and
    writes 0 rows; the batch goes on to the next date."""
    seed = await seeded(world, DAY, NEXT_DAY)
    written, hold = asyncio.Event(), asyncio.Event()

    async with background(world.run(world.refresh(wait=[AFTER_CLAIM]))) as run:
        await world.gate.reached(AFTER_CLAIM)
        world.clock.advance(MINUTE)
        world.gate.release(AFTER_CLAIM)
        async with background(
            world.write_source(
                seed, change=later_activity(600), written=written, hold=hold
            )
        ) as write:
            await written.wait()
            async with asyncio.timeout(20):
                while await waiting_query(world, "UPDATE analytics_daily_markers") == 0:
                    await asyncio.sleep(0.02)
            hold.set()
            await write
        report = await run

    assert report.failures["lock_timeout"] == 1
    assert report.saved == 1
    marker = await world.marker(seed.account_id, DAY)
    assert marker is not None
    assert (marker["attempts"], marker["last_error"]) == (0, None)
    assert await world.stored(seed.account_id, DAY) is None
    assert await world.stored(seed.account_id, NEXT_DAY) is not None
    world.clock.advance(MINUTE)
    await world.run()
    await assert_current(world, seed, DAY, NEXT_DAY)


async def waiting_query(world: BatchWorld, prefix: str) -> int:
    async with world.engine.connect() as connection:
        return await connection.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                " AND query LIKE :prefix"
            ),
            {"prefix": f"{prefix}%"},
        )


async def test_fi9_source_read_statement_timeout_is_recorded_and_backed_off(
    world: BatchWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FI-9: a test-only table lock makes the source read time out (57014).
    The failure class and backoff are recorded; after the lock it saves."""
    monkeypatch.setattr(daily_stats_batch, "SOURCE_STATEMENT_TIMEOUT", "500ms")
    seed = await seeded(world)

    async with world.engine.connect() as holder:
        await holder.execute(
            text("LOCK TABLE activity_timeline_segments IN ACCESS EXCLUSIVE MODE")
        )
        report = await world.run()
        await holder.rollback()

    assert report.failures["statement_timeout"] == 1
    marker = await world.marker(seed.account_id, DAY)
    assert marker is not None
    assert marker["last_error"] == "QueryCanceledError"
    assert marker["next_attempt_at"] == world.clock() + RETRY_POLICY.delay_after(1)
    await world.make_due()
    assert (await world.run()).saved == 1
    await assert_current(world, seed)


async def test_fi10_definition_mismatch_at_start_claims_nothing(
    world: BatchWorld,
) -> None:
    """FI-10: the database has definition 1 and the code definition 2. The run
    ends before claiming and counts one definition mismatch."""
    seed = await seeded(world)

    report = await world.run(world.refresh(definition=DEFINITION_2))

    assert (report.definition_mismatch, report.saved) == (1, 0)
    marker = await world.marker(seed.account_id, DAY)
    assert marker is not None
    assert marker["attempts"] == 0


async def test_fi11_definition_change_during_a_run_rolls_back_the_later_date(
    world: BatchWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FI-11: two dates claimed; the definition UPDATE races the first save.
    The first date is saved with definition 1, the second rolls back; after
    reprocessing both are definition 2."""
    seed = await seeded(world, DAY, NEXT_DAY)

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
            world.gate.release(BEFORE_SAVE_COMMIT)
            await update
            report = await run

    assert (report.saved, report.aborted) == (1, "definition")
    first = await world.stored(seed.account_id, DAY)
    assert first is not None
    assert first.definition_version == 1
    assert await world.stored(seed.account_id, NEXT_DAY) is None
    world.clock.advance(MINUTE)
    assert (await world.mark(definition=DEFINITION_2)).markers == 1
    await world.make_due()
    assert (await world.run(world.refresh(definition=DEFINITION_2))).saved == 2
    for day in (DAY, NEXT_DAY):
        stored = await world.stored(seed.account_id, day)
        assert stored is not None
        assert stored.definition_version == 2
    monkeypatch.setattr(reads, "CURRENT_DEFINITION", DEFINITION_2)
    await assert_current(world, seed, DAY, NEXT_DAY)


async def unmarked_old_rows(world: BatchWorld) -> int:
    async with world.engine.connect() as connection:
        return await connection.scalar(
            text(
                "SELECT count(*) FROM analytics_daily_stats s"
                " WHERE s.definition_version < 2 AND NOT EXISTS ("
                " SELECT 1 FROM analytics_daily_markers m"
                " WHERE m.account_id = s.account_id AND m.local_date = s.local_date)"
            )
        )


async def test_fi12_marks_before_raising_the_definition_row_leave_old_rows(
    world: BatchWorld,
) -> None:
    """FI-12 (contrast): marking before the definition row rises lets an old
    code batch handle the marks with definition 1, so old definition rows
    are left without a marker. Running the marks again fixes it."""
    seed = await seeded(world)
    await world.run(world.refresh(definition=DEFINITION_1))
    world.clock.advance(MINUTE)

    assert (await world.mark(definition=DEFINITION_2)).markers == 1
    old_batch = await world.run(world.refresh(definition=DEFINITION_1))
    assert old_batch.saved == 1
    assert await unmarked_old_rows(world) == 1

    await world.set_definition_row(2)
    world.clock.advance(MINUTE)
    assert (await world.mark(definition=DEFINITION_2)).markers == 1
    assert (await world.run(world.refresh(definition=DEFINITION_2))).saved == 1
    assert await unmarked_old_rows(world) == 0
    stored = await world.stored(seed.account_id)
    assert stored is not None
    assert stored.definition_version == 2


class HoldingMarks(RecomputeMarkerRepository):
    """Holds the mark transaction (and the account lock) after its upsert."""

    def __init__(self, release: asyncio.Event) -> None:
        self.holding = asyncio.Event()
        self.release = release
        self.version: int | None = None

    async def upsert_definition(self, db_session, *, account_id, local_date, **values):
        await super().upsert_definition(
            db_session, account_id=account_id, local_date=local_date, **values
        )
        self.version = await db_session.scalar(
            text(
                "SELECT version FROM analytics_daily_markers"
                " WHERE account_id = :account AND local_date = :day"
            ),
            {"account": account_id, "day": local_date},
        )
        self.holding.set()
        await self.release.wait()


async def test_fi14_source_write_waits_for_the_mark_and_draws_a_larger_version(
    world: BatchWorld,
) -> None:
    """FI-14: the mark holds the account lock; a label change of the same
    account waits, commits after the mark and gets a larger version. Held
    over 3 seconds, the source write fails with the lock timeout instead."""
    seed = await seeded(world)
    await world.run()
    await world.set_definition_row(2)
    world.clock.advance(MINUTE)
    release = asyncio.Event()
    marks = HoldingMarks(release)

    async with background(world.mark(definition=DEFINITION_2, markers=marks)) as mark:
        await marks.holding.wait()
        async with background(
            world.write_source(seed, change=later_activity(600))
        ) as write:
            await world.wait_for_lock_waiter()
            release.set()
            await mark
            source_version = await write

    assert marks.version is not None
    assert source_version > marks.version
    marker = await world.marker(seed.account_id)
    assert marker is not None
    assert marker["kind"] == MarkerKind.SOURCE

    contrast = HoldingMarks(asyncio.Event())
    async with background(world.mark(definition=DEFINITION_2, markers=contrast)):
        await contrast.holding.wait()
        with pytest.raises(ActivityTimelineBusyError):
            await world.write_source(seed, change=later_activity(620))
