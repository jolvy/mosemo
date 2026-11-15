from datetime import timedelta
from unittest.mock import Mock

import pytest

from mosemo.analytics.bootstrap import AnalyticsBatchConfig
from mosemo.entrypoints.batch.registry import build_jobs, ensure_unique_names
from mosemo.platform.scheduling import BatchJob

ANALYTICS = AnalyticsBatchConfig(reprocess_per_run=600)


async def noop() -> None:
    pass


def jobs_by_name() -> dict[str, BatchJob]:
    jobs = build_jobs(Mock(), label_model="openai:fake-model", analytics=ANALYTICS)
    return {job.name: job for job in jobs}


def test_registry_includes_the_label_proposal_job() -> None:
    job = jobs_by_name()["label-proposals"]

    assert job.schedule is not None
    assert job.schedule.run_on_start is True
    assert job.schedule.interval == timedelta(minutes=5)


def test_registry_includes_the_analytics_jobs() -> None:
    jobs = jobs_by_name()

    daily = jobs["analytics-daily-stats"]
    assert daily.schedule is not None
    assert daily.schedule.run_on_start is True
    assert daily.schedule.interval == timedelta(minutes=5)
    # The definition marks run only by hand.
    assert jobs["analytics-definition-mark"].schedule is None
    assert set(jobs) == {
        "label-proposals",
        "analytics-daily-stats",
        "analytics-definition-mark",
    }


def test_registry_rejects_malformed_label_model() -> None:
    with pytest.raises(ValueError, match="provider prefix"):
        build_jobs(Mock(), label_model="test", analytics=ANALYTICS)


def test_duplicate_job_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="same"):
        ensure_unique_names([BatchJob("same", noop), BatchJob("same", noop)])
