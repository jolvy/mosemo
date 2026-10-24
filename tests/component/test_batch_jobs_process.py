"""Verify the real batch job process contract without a database."""

import asyncio
import json
import os
import runpy
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DB_PASSWORD = "batch-jobs-secret-password"


def process_environment(label_model: str | None):
    # Close the socket after reserving the port: a bound but unclosed socket makes
    # macOS time out instead of refusing the connection.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
    environment = os.environ.copy()
    environment.update(
        runpy.run_path(str(ROOT / "scripts/export_openapi.py"))["EXPORT_ENVIRONMENT"]
    )
    environment.update(
        MOSEMO_ENV="test",
        DB_HOST="127.0.0.1",
        DB_PORT=str(port),
        DB_PASSWORD=DB_PASSWORD,
    )
    environment.pop("LABEL_MODEL", None)
    if label_model is not None:
        environment["LABEL_MODEL"] = label_model
    return environment


def run_process(module: str, *arguments: str, label_model: str | None):
    return subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=ROOT,
        env=process_environment(label_model),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines()]


def assert_no_leak(result: subprocess.CompletedProcess[str]) -> None:
    assert "Traceback" not in result.stderr
    assert DB_PASSWORD not in result.stdout + result.stderr


@pytest.mark.asyncio
async def test_serve_runs_label_job_on_start_isolates_failure_and_stops_on_sigterm():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mosemo.batch_jobs",
        "serve",
        cwd=ROOT,
        env=process_environment("openai:fake-model"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout = process.stdout
        assert stdout is not None

        async def wait_for_event(matches) -> dict[str, object]:
            while True:
                line = await stdout.readline()
                assert line, "process exited before the expected log"
                payload = json.loads(line)
                if matches(payload):
                    return payload

        started = await asyncio.wait_for(
            wait_for_event(
                lambda payload: payload["event"] == "batch job server started"
            ),
            15,
        )
        assert started["operation"] == "batch_jobs"
        failure = await asyncio.wait_for(
            wait_for_event(
                lambda payload: payload.get("operation") == "label-proposals"
            ),
            15,
        )
        assert failure["outcome"] == "failure"

        process.send_signal(signal.SIGTERM)
        output, errors = await asyncio.wait_for(process.communicate(), 15)
        assert process.returncode == 0
        assert b"Traceback" not in errors
        assert DB_PASSWORD.encode() not in output + errors
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def test_run_reports_job_failure_with_exit_code_one():
    result = run_process(
        "mosemo.batch_jobs", "run", "label-proposals", label_model="openai:fake-model"
    )

    assert result.returncode == 1, result.stdout + result.stderr
    (failure,) = [
        payload
        for payload in json_lines(result.stdout)
        if payload.get("operation") == "label-proposals"
    ]
    assert failure["outcome"] == "failure"
    assert_no_leak(result)


def test_run_reports_job_success_with_exit_code_zero():
    result = run_process(
        "tests.batch_jobs_process", "run", "noop", label_model="openai:fake-model"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    (success,) = json_lines(result.stdout)
    assert (success["operation"], success["outcome"]) == ("noop", "success")
    assert_no_leak(result)


def test_run_lists_available_jobs_for_an_unknown_name():
    result = run_process(
        "mosemo.batch_jobs", "run", "no-such-job", label_model="openai:fake-model"
    )

    assert result.returncode == 2
    assert result.stderr == "Unknown batch job; available jobs: label-proposals\n"
    assert_no_leak(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "exit_code"), [(["serve"], 0), (["run", "stubborn"], 1)]
)
async def test_job_ignoring_cancellation_does_not_outlive_the_shutdown_deadline(
    arguments, exit_code
):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.batch_jobs_process",
        *arguments,
        cwd=ROOT,
        env=process_environment("openai:fake-model"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout = process.stdout
        assert stdout is not None
        while (line := await asyncio.wait_for(stdout.readline(), 15)) != (
            b"STUBBORN_STARTED\n"
        ):
            assert line, "process exited before the job started"

        process.send_signal(signal.SIGTERM)
        output, errors = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == exit_code, output + errors
        assert errors == b""
        assert any(
            payload["level"] == "WARNING"
            and "did not stop before the shutdown timeout" in str(payload["event"])
            for payload in json_lines(output.decode())
        )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize("label_model", [None, "test"])
def test_missing_or_malformed_label_model_fails_with_a_fixed_message(label_model):
    result = run_process("mosemo.batch_jobs", "serve", label_model=label_model)

    assert result.returncode == 1
    assert result.stderr == "Batch job process failed\n"
    assert result.stdout == ""
    assert_no_leak(result)
