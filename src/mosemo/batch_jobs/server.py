"""Periodic batch job server backed by an in-memory APScheduler."""

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.util import undefined

from mosemo.batch_jobs.job import BatchJob, execute_job

logger = logging.getLogger(__name__)


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
    jobs: Sequence[BatchJob], running: set[asyncio.Task[object]]
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=UTC)
    for job in jobs:
        if job.schedule is None:
            continue
        scheduler.add_job(
            _tracked(job, running),
            job.schedule.trigger,
            id=job.name,
            name=job.name,
            max_instances=1,
            coalesce=True,
            # None runs a late tick once (coalesced) instead of dropping it.
            misfire_grace_time=None,
            next_run_time=datetime.now(UTC) if job.schedule.run_on_start else undefined,
        )
    return scheduler


async def serve(
    jobs: Sequence[BatchJob], stop: asyncio.Event, *, shutdown_timeout: float = 10.0
) -> None:
    running: set[asyncio.Task[object]] = set()
    scheduler = build_scheduler(jobs, running)
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
