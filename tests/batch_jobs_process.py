"""Run the real batch job entrypoint with a registry of test jobs.

Only registry assembly and the shutdown timeout are replaced; argument parsing,
logging, the execution wrapper and exit codes run the real code without a
database. `stubborn` ignores cancellation and starts as soon as `serve` starts.
"""

import asyncio
import runpy
import sys
from datetime import timedelta
from functools import partial
from unittest.mock import patch

from mosemo.platform import scheduling
from mosemo.platform.scheduling import BatchJob, every


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
    return [
        BatchJob("noop", noop),
        BatchJob("stubborn", stubborn, every(timedelta(minutes=5), run_on_start=True)),
    ]


if __name__ == "__main__":
    with (
        patch("mosemo.batch.registry.build_jobs", build_jobs),
        patch.object(
            scheduling,
            "run_job_once",
            partial(scheduling.run_job_once, shutdown_timeout=0.5),
        ),
        patch.object(
            scheduling, "serve", partial(scheduling.serve, shutdown_timeout=0.5)
        ),
    ):
        sys.argv = ["mosemo.batch", *sys.argv[1:]]
        runpy.run_module("mosemo.batch", run_name="__main__")
