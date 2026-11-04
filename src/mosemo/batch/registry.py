"""Every batch job is registered here; add a job with one line in build_jobs."""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.activity_labels.proposals.job import build_label_proposal_job
from mosemo.identity import bootstrap as identity_bootstrap
from mosemo.infra.config import Config
from mosemo.infra.scheduling import BatchJob


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
    # Every context that owns tables maps them before a job touches the database.
    identity_bootstrap.map_tables()
    jobs = [build_label_proposal_job(session_factory, model=config.label_model)]
    ensure_unique_names(jobs)
    return jobs
