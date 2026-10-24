"""Every batch job is registered here; add a job with one line in build_jobs."""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.activity_labels.proposals.job import build_label_proposal_job
from mosemo.batch_jobs.job import BatchJob
from mosemo.config import Config


def ensure_unique_names(jobs: Sequence[BatchJob]) -> None:
    names = [job.name for job in jobs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Duplicate batch job names: {', '.join(duplicates)}")


def build_jobs(
    session_factory: async_sessionmaker[AsyncSession], config: Config
) -> list[BatchJob]:
    if not config.label_model:
        raise ValueError("LABEL_MODEL is required to run batch jobs")
    jobs = [build_label_proposal_job(session_factory, model=config.label_model)]
    ensure_unique_names(jobs)
    return jobs
