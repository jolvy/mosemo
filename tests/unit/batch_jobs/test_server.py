import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest
from apscheduler.triggers.interval import IntervalTrigger

from mosemo.platform.clock import utc_now
from mosemo.platform.scheduling import BatchJob, build_scheduler, every, serve


async def noop() -> None:
    pass


@pytest.mark.asyncio
async def test_only_scheduled_jobs_are_registered_without_overlap() -> None:
    jobs = [
        BatchJob("on-start", noop, every(timedelta(minutes=5), run_on_start=True)),
        BatchJob("later", noop, every(timedelta(hours=1))),
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

    assert set(registered) == {"on-start", "later"}
    intervals = {"on-start": timedelta(minutes=5), "later": timedelta(hours=1)}
    for name, job in registered.items():
        assert isinstance(job.trigger, IntervalTrigger)
        assert job.trigger.interval == intervals[name]
        assert job.trigger.timezone == UTC
        assert job.max_instances == 1
        assert job.coalesce is True
        assert job.misfire_grace_time is None
    assert registered["on-start"].next_run_time == now
    assert registered["later"].next_run_time > after


@pytest.mark.parametrize("interval", [timedelta(0), timedelta(seconds=-1)])
def test_every_rejects_non_positive_interval(interval: timedelta) -> None:
    with pytest.raises(ValueError, match="positive"):
        every(interval)


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

    caplog.set_level(logging.INFO, logger="mosemo.platform.scheduling")
    server = asyncio.create_task(
        serve(
            [BatchJob("hold", hold, every(timedelta(minutes=5), run_on_start=True))],
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
        if record.name == "mosemo.platform.scheduling" and hasattr(record, "outcome")
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

    server = asyncio.create_task(
        serve(
            [
                BatchJob(
                    "stubborn", stubborn, every(timedelta(minutes=5), run_on_start=True)
                )
            ],
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
