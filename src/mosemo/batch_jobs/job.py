"""Batch job definitions and the execution wrapper shared by every run mode."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apscheduler.triggers.base import BaseTrigger

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Schedule:
    # Create triggers with timezone=UTC: APScheduler 3 triggers default to the
    # host's local timezone, not the scheduler's.
    trigger: BaseTrigger
    run_on_start: bool = False


@dataclass(frozen=True)
class BatchJob:
    """A job must be safe to rerun and to run concurrently with another process."""

    name: str
    run: Callable[[], Awaitable[None]]
    schedule: Schedule | None = None


def _result(job: BatchJob, outcome: str, started: float) -> dict[str, object]:
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
        logger.warning("batch job cancelled", extra=_result(job, "cancelled", started))
        raise
    except Exception:
        # The formatter records only the exception type and location, never its
        # message, so job inputs do not reach the logs.
        logger.exception("batch job failed", extra=_result(job, "failure", started))
        return False
    logger.info("batch job completed", extra=_result(job, "success", started))
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
    await asyncio.wait({task}, timeout=shutdown_timeout)
    return False
