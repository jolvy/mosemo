"""Label proposal batch job."""

from datetime import UTC

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.activity_labels.proposals.processing import (
    ProposalProcessor,
    ProposalScanner,
)
from mosemo.activity_labels.proposals.suggestions import PydanticAILabelSuggester
from mosemo.batch_jobs.job import BatchJob, Schedule
from mosemo.clock import utc_now

LABEL_PROPOSAL_JOB_NAME = "label-proposals"
LABEL_PROPOSAL_INTERVAL_MINUTES = 5


def build_label_proposal_job(
    session_factory: async_sessionmaker[AsyncSession], *, model: str
) -> BatchJob:
    # Constructed up front so an invalid LABEL_MODEL fails before the first run.
    suggester = PydanticAILabelSuggester(model)

    async def run() -> None:
        # A fresh scanner starts its cursor over, so each run is a full sweep.
        processor = ProposalProcessor(
            session_factory, suggester=suggester, clock=utc_now
        )
        await ProposalScanner(session_factory, processor, clock=utc_now).run_sweep()

    return BatchJob(
        LABEL_PROPOSAL_JOB_NAME,
        run,
        Schedule(
            IntervalTrigger(minutes=LABEL_PROPOSAL_INTERVAL_MINUTES, timezone=UTC),
            run_on_start=True,
        ),
    )
