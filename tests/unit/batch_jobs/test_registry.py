from datetime import UTC, timedelta
from unittest.mock import Mock

import pytest
from apscheduler.triggers.interval import IntervalTrigger

from mosemo.batch_jobs.job import BatchJob
from mosemo.batch_jobs.registry import build_jobs, ensure_unique_names
from mosemo.config import Config


async def noop() -> None:
    pass


def test_registry_includes_the_label_proposal_job(config: Config) -> None:
    config.label_model = "openai:fake-model"

    (job,) = build_jobs(Mock(), config)

    assert job.name == "label-proposals"
    assert job.schedule is not None
    assert job.schedule.run_on_start is True
    trigger = job.schedule.trigger
    assert isinstance(trigger, IntervalTrigger)
    assert trigger.interval == timedelta(minutes=5)
    assert trigger.timezone == UTC


@pytest.mark.parametrize(
    ("label_model", "message"),
    [(None, "LABEL_MODEL is required"), ("test", "provider prefix")],
)
def test_registry_rejects_missing_or_malformed_label_model(
    config: Config, label_model: str | None, message: str
) -> None:
    config.label_model = label_model

    with pytest.raises(ValueError, match=message):
        build_jobs(Mock(), config)


def test_duplicate_job_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="same"):
        ensure_unique_names([BatchJob("same", noop), BatchJob("same", noop)])
