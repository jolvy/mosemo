"""Verify process exit, not FastStream's partial-start resource cleanup."""

import asyncio
import os
import runpy
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def process_environment(bootstrap_port):
    environment = os.environ.copy()
    environment.update(
        runpy.run_path(str(ROOT / "scripts/export_openapi.py"))["EXPORT_ENVIRONMENT"]
    )
    environment.update(
        MOSEMO_ENV="test",
        BATCH_ENABLED="true",
        BATCH_BOOTSTRAP_SERVERS=f"127.0.0.1:{bootstrap_port}",
        BATCH_TOPIC="test.startup-exit",
        BATCH_GROUP_ID="test.startup-exit",
    )
    return environment


@pytest.mark.parametrize("entrypoint", ["api", "consumer"])
@pytest.mark.parametrize("scenario", ["unavailable", "failure", "cancel"])
def test_startup_error_or_task_cancellation_exits_real_entrypoint(entrypoint, scenario):
    # Reserve a loopback port without listening, so actual Kafka connection fails.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        environment = process_environment(unavailable.getsockname()[1])
        if scenario == "unavailable":
            command = (
                [
                    "uvicorn",
                    "mosemo.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--lifespan",
                    "on",
                ]
                if entrypoint == "api"
                else ["mosemo.activities.consumer"]
            )
        else:
            command = ["tests.messaging_startup_process", entrypoint, scenario]
        result = subprocess.run(
            [sys.executable, "-m", *command],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    assert result.returncode > 0, result.stdout + result.stderr
    assert "Application startup complete" not in result.stderr
    if scenario != "unavailable":
        assert result.stdout.count("KAFKA_START_ATTEMPT") == 1
    if scenario == "cancel":
        assert "KAFKA_START_CANCELLED" in result.stdout
    if entrypoint == "consumer":
        assert result.stderr.endswith(
            "Activity Consumer failed; exiting without an in-process retry\n"
        )


@pytest.mark.asyncio
async def test_sigterm_cancels_consumer_startup_and_exits_without_retry():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.messaging_startup_process",
        "consumer",
        "hold",
        cwd=ROOT,
        env=process_environment(1),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert (
            await asyncio.wait_for(process.stdout.readline(), 10)
            == b"KAFKA_START_ATTEMPT\n"
        )
        process.send_signal(signal.SIGTERM)
        output, errors = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 0
        assert output == b"KAFKA_START_CANCELLED\n"
        assert errors == b""
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
