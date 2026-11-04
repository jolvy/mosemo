"""Label proposal batch job."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.activity_labels.proposals.processing import (
    ProposalProcessor,
    ProposalScanner,
)
from mosemo.activity_labels.proposals.suggestions import PydanticAILabelSuggester
from mosemo.infra.clock import utc_now
from mosemo.infra.scheduling import BatchJob, every
from mosemo.labels.public import LabelCatalog

LABEL_PROPOSAL_JOB_NAME = "label-proposals"
LABEL_PROPOSAL_INTERVAL_MINUTES = 5


def build_label_proposal_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    model: str,
    labels: LabelCatalog,
) -> BatchJob:
    # Constructed up front so an invalid LABEL_MODEL fails before the first run.
    suggester = PydanticAILabelSuggester(model)

    async def run() -> None:
        # A fresh scanner starts its cursor over, so each run is a full sweep.
        processor = ProposalProcessor(
            session_factory, suggester=suggester, clock=utc_now, labels=labels
        )
        await ProposalScanner(session_factory, processor, clock=utc_now).run_sweep()

    return BatchJob(
        LABEL_PROPOSAL_JOB_NAME,
        run,
        every(timedelta(minutes=LABEL_PROPOSAL_INTERVAL_MINUTES), run_on_start=True),
    )
