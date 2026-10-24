import asyncio
import logging

import pytest

from mosemo.batch_jobs.job import BatchJob, execute_job, run_job_once
from mosemo.logging import OPTIONAL_FIELDS

STANDARD_RECORD_FIELDS = set(vars(logging.makeLogRecord({}))) | {"message"}


def job_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record for record in caplog.records if record.name == "mosemo.batch_jobs.job"
    ]


def assert_allowed_fields(record: logging.LogRecord) -> None:
    assert set(vars(record)) - STANDARD_RECORD_FIELDS <= set(OPTIONAL_FIELDS)


@pytest.mark.asyncio
async def test_failed_job_is_isolated_and_the_next_run_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("secret detail")

    job = BatchJob("flaky", flaky)
    caplog.set_level(logging.INFO, logger="mosemo.batch_jobs")

    assert await execute_job(job) is False
    assert await execute_job(job) is True

    failure, success = job_records(caplog)
    assert (failure.levelno, vars(failure)["operation"], vars(failure)["outcome"]) == (
        logging.ERROR,
        "flaky",
        "failure",
    )
    assert failure.exc_info is not None
    assert (success.levelno, vars(success)["operation"], vars(success)["outcome"]) == (
        logging.INFO,
        "flaky",
        "success",
    )
    for record in (failure, success):
        assert isinstance(vars(record)["duration_ms"], int)
        assert "secret detail" not in record.getMessage()
        assert_allowed_fields(record)


@pytest.mark.asyncio
async def test_run_job_once_returns_job_result_without_a_schedule() -> None:
    async def succeed() -> None:
        pass

    async def fail() -> None:
        raise RuntimeError("boom")

    assert await run_job_once(BatchJob("succeed", succeed), asyncio.Event()) is True
    assert await run_job_once(BatchJob("fail", fail), asyncio.Event()) is False


@pytest.mark.asyncio
async def test_run_job_once_cancels_the_job_when_stopped(
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
            cleaned_up.set()

    caplog.set_level(logging.INFO, logger="mosemo.batch_jobs")
    run = asyncio.create_task(run_job_once(BatchJob("hold", hold), stop))
    await started.wait()
    stop.set()

    assert await run is False
    assert cleaned_up.is_set()
    (record,) = job_records(caplog)
    assert (vars(record)["operation"], vars(record)["outcome"]) == ("hold", "cancelled")
    assert_allowed_fields(record)
