"""Run the real batch job entrypoint with a registry of test jobs.

Only registry assembly and the shutdown timeout are replaced; argument parsing,
logging, the execution wrapper and exit codes run the real code without a
database. `stubborn` ignores cancellation and starts as soon as `serve` starts.
"""

import asyncio
import runpy
import sys
from datetime import UTC
from functools import partial
from unittest.mock import patch

from apscheduler.triggers.interval import IntervalTrigger

from mosemo.batch_jobs import job, server
from mosemo.batch_jobs.job import BatchJob, Schedule


async def noop() -> None:
    pass


async def stubborn() -> None:
    print("STUBBORN_STARTED", flush=True)
    while True:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            continue


def build_jobs(session_factory, config) -> list[BatchJob]:
    trigger = IntervalTrigger(minutes=5, timezone=UTC)
    return [
        BatchJob("noop", noop),
        BatchJob("stubborn", stubborn, Schedule(trigger, run_on_start=True)),
    ]


if __name__ == "__main__":
    with (
        patch("mosemo.batch_jobs.registry.build_jobs", build_jobs),
        patch.object(
            job, "run_job_once", partial(job.run_job_once, shutdown_timeout=0.5)
        ),
        patch.object(server, "serve", partial(server.serve, shutdown_timeout=0.5)),
    ):
        sys.argv = ["mosemo.batch_jobs", *sys.argv[1:]]
        runpy.run_module("mosemo.batch_jobs", run_name="__main__")
