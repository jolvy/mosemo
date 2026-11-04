"""Batch job definitions, the execution wrapper, and the APScheduler server.

APScheduler is imported only here: job definitions build a `Schedule` with the
creation functions below, and only this module turns it into a trigger.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.util import undefined

from mosemo.shared.application.clock import Clock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Schedule:
    """When a job runs, without APScheduler types; build it with `every`."""

    interval: timedelta
    run_on_start: bool = False


def every(interval: timedelta, *, run_on_start: bool = False) -> Schedule:
    """Run at a fixed interval."""

    if interval <= timedelta(0):
        raise ValueError("schedule interval must be positive")
    return Schedule(interval, run_on_start=run_on_start)


def _trigger(schedule: Schedule) -> BaseTrigger:
    # ADR 0010: create triggers with timezone=UTC. APScheduler 3 triggers
    # default to the host's local timezone, not the scheduler's.
    return IntervalTrigger(seconds=schedule.interval.total_seconds(), timezone=UTC)


@dataclass(frozen=True)
class BatchJob:
    """A job must be safe to rerun and to run concurrently with another process."""

    name: str
    run: Callable[[], Awaitable[None]]
    schedule: Schedule | None = None


def _result(job: BatchJob, outcome: str, *, started: float) -> dict[str, object]:
    return {
        "operation": job.name,
        "outcome": outcome,
        "duration_ms": round((perf_counter() - started) * 1000),
    }


async def execute_job(job: BatchJob) -> bool:
    started = perf_counter()
    try:
        await job.run()
    except asyncio.CancelledError:
        logger.warning(
            "batch job cancelled", extra=_result(job, "cancelled", started=started)
        )
        raise
    except Exception:
        # The formatter records only the exception type and location, never its
        # message, so job inputs do not reach the logs.
        logger.exception(
            "batch job failed", extra=_result(job, "failure", started=started)
        )
        return False
    logger.info("batch job completed", extra=_result(job, "success", started=started))
    return True


async def run_job_once(
    job: BatchJob, stop: asyncio.Event, *, shutdown_timeout: float = 10.0
) -> bool:
    task = asyncio.create_task(execute_job(job))
    stopped = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({task, stopped}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stopped.cancel()
    if task.done():
        return not task.cancelled() and task.result()
    task.cancel()
    _, pending = await asyncio.wait({task}, timeout=shutdown_timeout)
    if pending:
        logger.warning(
            "batch job did not stop before the shutdown timeout",
            extra={"operation": job.name},
        )
    return False


def _tracked(job: BatchJob, running: set[asyncio.Task[object]]):
    async def run() -> None:
        task = asyncio.current_task()
        assert task is not None
        running.add(task)
        try:
            await execute_job(job)
        except asyncio.CancelledError:
            # execute_job already logged it. APScheduler would discard the
            # cancellation anyway, after logging it as a job error.
            return
        finally:
            running.discard(task)

    return run


def build_scheduler(
    jobs: Sequence[BatchJob], running: set[asyncio.Task[object]], *, now: datetime
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=UTC)
    for job in jobs:
        if job.schedule is None:
            continue
        scheduler.add_job(
            _tracked(job, running),
            _trigger(job.schedule),
            id=job.name,
            name=job.name,
            max_instances=1,
            coalesce=True,
            # None runs a late tick once (coalesced) instead of dropping it.
            misfire_grace_time=None,
            next_run_time=now if job.schedule.run_on_start else undefined,
        )
    return scheduler


async def serve(
    jobs: Sequence[BatchJob],
    stop: asyncio.Event,
    *,
    clock: Clock,
    shutdown_timeout: float = 10.0,
) -> None:
    running: set[asyncio.Task[object]] = set()
    scheduler = build_scheduler(jobs, running, now=clock())
    scheduler.start()
    logger.info("batch job server started", extra={"operation": "batch_jobs"})
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)
        # APScheduler cancels running jobs without awaiting them, so wait here
        # before the caller disposes resources the jobs still use.
        tasks = set(running)
        for task in tasks:
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=shutdown_timeout)
            if pending:
                logger.warning(
                    "%d batch jobs did not stop before the shutdown timeout",
                    len(pending),
                    extra={"operation": "batch_jobs"},
                )
