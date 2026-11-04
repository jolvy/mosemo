from datetime import timedelta
from unittest.mock import Mock

import pytest

from mosemo.batch.registry import build_jobs, ensure_unique_names
from mosemo.infra.config import Config
from mosemo.infra.scheduling import BatchJob


async def noop() -> None:
    pass


def test_registry_includes_the_label_proposal_job(config: Config) -> None:
    config.label_model = "openai:fake-model"

    (job,) = build_jobs(Mock(), config)

    assert job.name == "label-proposals"
    assert job.schedule is not None
    assert job.schedule.run_on_start is True
    assert job.schedule.interval == timedelta(minutes=5)


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
