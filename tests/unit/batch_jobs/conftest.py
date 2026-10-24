import logging

import pytest


@pytest.fixture(autouse=True)
def enabled_batch_job_loggers(monkeypatch: pytest.MonkeyPatch) -> None:
    # Alembic's fileConfig in integration tests disables loggers created earlier.
    for name in (
        "mosemo.batch_jobs.job",
        "mosemo.batch_jobs.server",
        "apscheduler.executors.default",
    ):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
