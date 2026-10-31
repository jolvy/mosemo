import asyncio
import logging
from datetime import UTC, datetime

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from mosemo.batch_jobs.job import BatchJob, Schedule
from mosemo.batch_jobs.server import build_scheduler, serve
from mosemo.clock import utc_now


async def noop() -> None:
    pass


@pytest.mark.asyncio
async def test_only_scheduled_jobs_are_registered_without_overlap() -> None:
    interval = IntervalTrigger(minutes=5, timezone=UTC)
    cron = CronTrigger(hour=3, timezone=UTC)
    jobs = [
        BatchJob("interval", noop, Schedule(interval, run_on_start=True)),
        BatchJob("cron", noop, Schedule(cron)),
        BatchJob("manual", noop),
    ]

    now = datetime(2026, 9, 1, tzinfo=UTC)
    scheduler = build_scheduler(jobs, set(), now=now)
    after = utc_now()
    scheduler.start(paused=True)
    try:
        registered = {job.id: job for job in scheduler.get_jobs()}
    finally:
        scheduler.shutdown(wait=False)

    assert set(registered) == {"interval", "cron"}
    assert registered["interval"].trigger is interval
    assert registered["cron"].trigger is cron
    for job in registered.values():
        assert job.max_instances == 1
        assert job.coalesce is True
        assert job.misfire_grace_time is None
    assert registered["interval"].next_run_time == now
    assert registered["cron"].next_run_time > after


@pytest.mark.asyncio
async def test_stop_cancels_running_job_and_waits_for_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()
    cleaned_up = asyncio.Event()
    stop = asyncio.Event()

    async def hold() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned_up.set()

    trigger = IntervalTrigger(minutes=5, timezone=UTC)
    caplog.set_level(logging.INFO, logger="mosemo.batch_jobs")
    server = asyncio.create_task(
        serve(
            [BatchJob("hold", hold, Schedule(trigger, run_on_start=True))],
            stop,
            clock=utc_now,
        )
    )
    await asyncio.wait({asyncio.create_task(started.wait())}, timeout=5)
    assert started.is_set()
    stop.set()
    done, _ = await asyncio.wait({server}, timeout=5)

    assert server in done
    assert server.result() is None
    assert cleaned_up.is_set()
    outcomes = [
        (vars(record)["operation"], vars(record)["outcome"])
        for record in caplog.records
        if record.name == "mosemo.batch_jobs.job"
    ]
    assert outcomes == [("hold", "cancelled")]
    assert not [
        record
        for record in caplog.records
        if record.name.startswith("apscheduler") and record.levelno >= logging.ERROR
    ]


@pytest.mark.asyncio
async def test_stop_gives_up_on_a_job_that_ignores_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    stop = asyncio.Event()

    async def stubborn() -> None:
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    trigger = IntervalTrigger(minutes=5, timezone=UTC)
    server = asyncio.create_task(
        serve(
            [BatchJob("stubborn", stubborn, Schedule(trigger, run_on_start=True))],
            stop,
            clock=utc_now,
            shutdown_timeout=0.1,
        )
    )
    try:
        await asyncio.wait({asyncio.create_task(started.wait())}, timeout=5)
        assert started.is_set()
        stop.set()
        done, _ = await asyncio.wait({server}, timeout=5)

        assert server in done
        assert server.result() is None
        assert any(
            record.levelno == logging.WARNING
            and record.getMessage()
            == "1 batch jobs did not stop before the shutdown timeout"
            for record in caplog.records
        )
    finally:
        release.set()
        await asyncio.sleep(0)
