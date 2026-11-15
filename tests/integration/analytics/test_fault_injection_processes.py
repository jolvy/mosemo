"""Fault injection on the real batch process (verification plan 「장애 주입」
FI-2~FI-5, FI-13, FI-15).

`python -m mosemo.entrypoints.batch run …` runs through
`tests.support.processes.analytics_batch` with the wait points switched on by
environment variables; the test sends SIGKILL or SIGTERM there. After a kill
no backend of the batch may stay in a transaction or hold a lock.
"""

import asyncio
import json
import signal
from datetime import timedelta
from time import perf_counter

import pytest
from sqlalchemy import text

from mosemo.analytics.application import reads
from mosemo.analytics.application.daily_stats_batch import (
    AFTER_CLAIM,
    AFTER_SOURCE_READ,
    BEFORE_SAVE_COMMIT,
)
from mosemo.analytics.domain.markers import MarkerKind
from tests.support.analytics_batch import (
    DAY,
    EDITOR,
    BatchProcess,
    BatchWorld,
    Gate,
    background,
    definition,
    finish,
    kill,
    later_activity,
)
from tests.support.analytics_sources import SourceSeed

pytestmark = [pytest.mark.fault_injection, pytest.mark.asyncio]

NEXT_DAY = DAY + timedelta(days=1)
MINUTE = timedelta(minutes=1)
DAILY_STATS = ("run", "analytics-daily-stats")
DEFINITION_MARK = ("run", "analytics-definition-mark")


async def seeded(world: BatchWorld, *days) -> SourceSeed:
    seed = await world.account()
    async with world.seeding(seed):
        for day in days or (DAY,):
            start = (day - DAY).days * 1440 + 540
            seed.activity(start, start + 30, EDITOR)
    await world.mark_days(seed, *(days or (DAY,)))
    return seed


def process(world: BatchWorld) -> BatchProcess:
    return BatchProcess(world.url, world.gate.directory)


async def assert_current(world: BatchWorld, seed: SourceSeed, *days) -> None:
    assert await world.marker_rows(seed.account_id) == []
    for day in days or (DAY,):
        stored = await world.stored(seed.account_id, day)
        assert stored is not None
        computed = await world.computed(seed.account_id, day)
        assert stored.stats == computed
        assert (await world.read(seed.account_id, day)).stats == computed


async def test_fi2_sigkill_during_the_calculation_blocks_no_source_write(
    world: BatchWorld,
) -> None:
    """FI-2: `kill -9` at the wait point after the source read; a source write
    of the account commits without waiting, its marker has a new version and
    0 attempts, and the next batch saves."""
    seed = await seeded(world)
    v1 = (await world.existing_marker(seed.account_id))["version"]
    batch = process(world).start(
        *DAILY_STATS, ANALYTICS_TEST_WAIT_POINTS=AFTER_SOURCE_READ
    )
    try:
        await world.gate.reached(AFTER_SOURCE_READ)
        await kill(batch)
    finally:
        batch.kill()
    world.clock.advance(MINUTE)
    started = perf_counter()
    v2 = await world.write_source(seed, change=later_activity(600))

    assert perf_counter() - started < 1
    marker = await world.marker(seed.account_id)
    assert marker is not None
    assert (marker["version"], marker["attempts"]) == (v2, 0)
    assert v2 > v1
    await world.wait_until_quiet()
    assert (await world.run()).saved == 1
    await assert_current(world, seed)


async def test_fi3_repeated_sigkill_quarantines_without_a_failure_record(
    world: BatchWorld,
) -> None:
    """FI-3: the calculation of one date kills the process every time, six
    runs. Attempt 6 and no failure record leave it out of the claim; the
    other date is saved."""
    seed = await seeded(world, NEXT_DAY, DAY)
    codes = []
    for _ in range(6):
        result = process(world).run(
            *DAILY_STATS,
            TEST_ANALYTICS_COMPUTE_FAULT="kill",
            TEST_ANALYTICS_FAULT_DATE=DAY.isoformat(),
        )
        codes.append(result.returncode)
        await world.wait_until_quiet()
        await world.make_due()

    assert codes == [-signal.SIGKILL] * 6
    assert await world.stored(seed.account_id, NEXT_DAY) is not None
    marker = await world.marker(seed.account_id, DAY)
    assert marker is not None
    assert (marker["attempts"], marker["last_error"]) == (6, None)
    report = await world.run()
    assert report.saved == 0
    assert (await world.existing_marker(seed.account_id, DAY))["attempts"] == 6


async def test_fi4_sigkill_inside_the_save_rolls_it_back_and_frees_the_write(
    world: BatchWorld,
) -> None:
    """FI-4: the save waits after its DELETE, before the commit; a source write
    waits for the marker row lock; `kill -9` lets the write commit at once
    and the stored row stays as it was."""
    seed = await seeded(world)
    await world.run()
    world.clock.advance(MINUTE)
    await world.write_source(seed, change=later_activity(600))
    before = await world.stored(seed.account_id)
    assert before is not None
    batch = process(world).start(
        *DAILY_STATS, ANALYTICS_TEST_WAIT_POINTS=BEFORE_SAVE_COMMIT
    )
    try:
        await world.gate.reached(BEFORE_SAVE_COMMIT)
        world.clock.advance(MINUTE)
        async with background(
            world.write_source(seed, change=later_activity(620))
        ) as write:
            await world.wait_for_lock_waiter()
            await kill(batch)
            await asyncio.wait_for(write, 3)
    finally:
        batch.kill()

    after = await world.stored(seed.account_id)
    assert after is not None
    assert (after.source_version, after.stats) == (before.source_version, before.stats)
    await world.wait_until_quiet()
    await world.make_due()
    await world.run()
    await assert_current(world, seed)


async def test_fi5_sigterm_cancels_rolls_back_and_the_next_run_saves(
    world: BatchWorld,
) -> None:
    """FI-5: `kill -TERM` at the wait point after the source read; the process
    stops within 10 seconds with the cancellation log, nothing is saved,
    and the next run saves."""
    seed = await seeded(world)
    batch = process(world).start(
        *DAILY_STATS, ANALYTICS_TEST_WAIT_POINTS=AFTER_SOURCE_READ
    )
    try:
        await world.gate.reached(AFTER_SOURCE_READ)
        batch.send_signal(signal.SIGTERM)
        code = await finish(batch, timeout=10)
        stdout = batch.stdout.read() if batch.stdout else ""
    finally:
        batch.kill()

    assert code == 1
    events = [json.loads(line)["event"] for line in stdout.splitlines()]
    assert "batch job cancelled" in events
    assert await world.stored(seed.account_id) is None
    await world.wait_until_quiet()
    await world.make_due()
    assert (await world.run()).saved == 1
    await assert_current(world, seed)


async def test_fi13_interrupted_mark_process_and_rerun_mark_every_date(
    world: BatchWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FI-13: 3 accounts × 3 stored dates; the mark process is killed inside
    the third account. Meanwhile reads compute from the source (definition
    mismatch); the rerun marks all 9 and no lock is left."""
    days = [DAY - timedelta(days=1), DAY, NEXT_DAY]
    seeds = [await seeded(world, *days) for _ in range(3)]
    await world.run()
    await world.set_definition_row(2)
    monkeypatch.setattr(reads, "CURRENT_DEFINITION", definition(2))

    killed = process(world).run(
        *DEFINITION_MARK,
        TEST_ANALYTICS_DEFINITION_VERSION="2",
        TEST_ANALYTICS_KILL_AFTER_ACCOUNTS="2",
    )
    assert killed.returncode == -signal.SIGKILL
    assert "MARK_KILLED_INSIDE_ACCOUNT" in killed.stdout
    await world.wait_until_quiet()
    assert len(await world.marker_rows()) == 6
    for seed in seeds:
        view = await world.read(seed.account_id)
        assert view.definition_version == 2
        assert view.stats == await world.computed(seed.account_id)

    rerun = process(world).run(*DEFINITION_MARK, TEST_ANALYTICS_DEFINITION_VERSION="2")

    assert rerun.returncode == 0, rerun.stderr
    markers = await world.marker_rows()
    assert len(markers) == 9
    assert {row["kind"] for row in markers} == {MarkerKind.DEFINITION}
    await world.wait_until_quiet()
    assert await world.held_locks() == 0


async def test_fi15_two_workers_claim_disjoint_dates_and_a_killed_ones_return(
    world: BatchWorld, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """FI-15: 100 markers and two batch processes at once. Their claims do not
    overlap (every marker has exactly one attempt); after one is killed the
    other saves its dates, and the killed one's dates come back after the
    lease."""
    days = [DAY - timedelta(days=offset) for offset in range(100)]
    seed = await world.account()
    await world.mark_days(seed, *days)
    gates = [Gate(tmp_path_factory.mktemp(name)) for name in ("first", "second")]
    workers = [BatchProcess(world.url, gate.directory) for gate in gates]

    first = workers[0].start(*DAILY_STATS, ANALYTICS_TEST_WAIT_POINTS=AFTER_CLAIM)
    second = None
    try:
        await gates[0].reached(AFTER_CLAIM)
        second = workers[1].start(*DAILY_STATS, ANALYTICS_TEST_WAIT_POINTS=AFTER_CLAIM)
        await gates[1].reached(AFTER_CLAIM)
        attempts = [row["attempts"] for row in await world.marker_rows()]
        assert attempts == [1] * 100
        await kill(first)
        gates[1].release(AFTER_CLAIM)
        assert await finish(second, timeout=60) == 0
    finally:
        first.kill()
        if second is not None:
            second.kill()

    left = await world.marker_rows()
    assert len(left) == 50
    assert all(row["attempts"] == 1 for row in left)
    await world.wait_until_quiet()
    await world.make_due()
    assert workers[1].run(*DAILY_STATS).returncode == 0
    assert await world.marker_rows() == []
    async with world.engine.connect() as connection:
        stored = await connection.scalar(
            text("SELECT count(*) FROM analytics_daily_stats WHERE account_id = :id"),
            {"id": seed.account_id},
        )
    assert stored == 100
