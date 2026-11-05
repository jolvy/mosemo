"""Every batch job is registered here; add a job with one line in build_jobs."""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.identity import bootstrap as identity_bootstrap
from mosemo.labeling import bootstrap as labeling_bootstrap
from mosemo.labels import bootstrap as labels_bootstrap
from mosemo.observation import bootstrap as observation_bootstrap
from mosemo.platform.providers import get_event_dispatcher
from mosemo.platform.scheduling import BatchJob


def ensure_unique_names(jobs: Sequence[BatchJob]) -> None:
    names = [job.name for job in jobs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Duplicate batch job names: {', '.join(duplicates)}")


def build_jobs(
    session_factory: async_sessionmaker[AsyncSession], *, label_model: str
) -> list[BatchJob]:
    # Every context that owns tables maps them before a job touches the database.
    identity_bootstrap.map_tables()
    labels_bootstrap.map_tables()
    observation_bootstrap.map_tables()
    labeling_bootstrap.map_tables()
    jobs = [
        labeling_bootstrap.create_label_proposal_job(
            session_factory,
            model=label_model,
            labels=labels_bootstrap.create_label_catalog(),
            timeline=observation_bootstrap.create_observation_timeline(),
            dispatcher=get_event_dispatcher(),
        )
    ]
    ensure_unique_names(jobs)
    return jobs
