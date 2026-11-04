import logging

import pytest


@pytest.fixture(autouse=True)
def enabled_batch_job_loggers(monkeypatch: pytest.MonkeyPatch) -> None:
    # Alembic's fileConfig in integration tests disables loggers created earlier.
    for name in (
        "mosemo.infra.scheduling",
        "apscheduler.executors.default",
    ):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
