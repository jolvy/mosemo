"""Run the real batch job entrypoint with a registry holding one no-op job.

Only registry assembly is replaced; argument parsing, logging, the execution
wrapper and exit codes run the real code without a database.
"""

import runpy
import sys
from unittest.mock import patch

from mosemo.batch_jobs.job import BatchJob


async def noop() -> None:
    pass


if __name__ == "__main__":
    with patch(
        "mosemo.batch_jobs.registry.build_jobs",
        lambda session_factory, config: [BatchJob("noop", noop)],
    ):
        sys.argv = ["mosemo.batch_jobs", *sys.argv[1:]]
        runpy.run_module("mosemo.batch_jobs", run_name="__main__")
