from datetime import timedelta
from unittest.mock import Mock

import pytest

from mosemo.entrypoints.batch.registry import build_jobs, ensure_unique_names
from mosemo.platform.scheduling import BatchJob


async def noop() -> None:
    pass


def test_registry_includes_the_label_proposal_job() -> None:
    (job,) = build_jobs(Mock(), label_model="openai:fake-model")

    assert job.name == "label-proposals"
    assert job.schedule is not None
    assert job.schedule.run_on_start is True
    assert job.schedule.interval == timedelta(minutes=5)


def test_registry_rejects_malformed_label_model() -> None:
    with pytest.raises(ValueError, match="provider prefix"):
        build_jobs(Mock(), label_model="test")


def test_duplicate_job_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="same"):
        ensure_unique_names([BatchJob("same", noop), BatchJob("same", noop)])
