"""Inject public Broker startup failures into real single-process entrypoints.

No raw Kafka client or library-private state is accessed. Network-failure tests
also run the entrypoints directly, without this public-method test double.
"""

import asyncio
import runpy
import sys
from unittest.mock import patch

from faststream.kafka import KafkaBroker


async def start(self):
    print("KAFKA_START_ATTEMPT", flush=True)
    if scenario == "failure":
        raise ConnectionError("injected Kafka startup failure")
    if scenario == "cancel":
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
    try:
        await asyncio.Event().wait()
    finally:
        print("KAFKA_START_CANCELLED", flush=True)


if __name__ == "__main__":
    entrypoint, scenario = sys.argv[1:]
    with patch.object(KafkaBroker, "start", start):
        if entrypoint == "api":
            sys.argv = [
                "uvicorn",
                "mosemo.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--lifespan",
                "on",
                "--no-access-log",
            ]
            runpy.run_module("uvicorn", run_name="__main__")
        else:
            runpy.run_module("mosemo.activities.consumer", run_name="__main__")
