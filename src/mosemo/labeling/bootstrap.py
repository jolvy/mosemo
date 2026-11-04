"""Labeling assembly for the assembly roots: mapping, object graph, router, job."""

from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.identity.public import AccountDirectory
from mosemo.infra.clock import utc_now
from mosemo.infra.scheduling import BatchJob, every
from mosemo.labeling.application.confirmations import (
    ConfirmSegmentLabel,
    ConfirmSegmentLabels,
    SegmentConfirmations,
)
from mosemo.labeling.application.ports import LabelSuggester
from mosemo.labeling.application.proposals import (
    ProposalAttempts,
    ProposeLabel,
    RecentConfirmedExampleRetriever,
    ScanLabelProposals,
    SuggestionInputs,
    SweepLabelProposals,
)
from mosemo.labeling.application.states import GetSegmentLabelState
from mosemo.labeling.application.timeline import GetLabelTimeline
from mosemo.labeling.infrastructure.confirmations import ConfirmationRepository
from mosemo.labeling.infrastructure.proposals import ProposalRepository
from mosemo.labeling.infrastructure.suggestions import PydanticAILabelSuggester
from mosemo.labeling.infrastructure.tables import map_tables
from mosemo.labeling.presentation.error_docs import ERROR_DOCS
from mosemo.labeling.presentation.router import create_router
from mosemo.labels.public import LabelCatalog
from mosemo.observation.public import ObservationTimeline
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher

__all__ = [
    "ERROR_DOCS",
    "LABEL_PROPOSAL_JOB_NAME",
    "LabelProposals",
    "Labeling",
    "create_label_proposal_job",
    "map_tables",
]

LABEL_PROPOSAL_JOB_NAME = "label-proposals"
LABEL_PROPOSAL_INTERVAL_MINUTES = 5


class Labeling:
    """Stateless labeling objects built once when an assembly root starts."""

    def __init__(
        self,
        *,
        accounts: AccountDirectory,
        labels: LabelCatalog,
        timeline: ObservationTimeline,
        dispatcher: EventDispatcher,
        clock: Clock,
    ) -> None:
        confirmations = ConfirmationRepository()
        segment_confirmations = SegmentConfirmations(
            confirmations=confirmations, proposals=ProposalRepository()
        )
        self.confirm_segment_label = ConfirmSegmentLabel(
            confirmations=segment_confirmations,
            labels=labels,
            timeline=timeline,
            dispatcher=dispatcher,
            clock=clock,
        )
        self.confirm_segment_labels = ConfirmSegmentLabels(
            confirmations=segment_confirmations,
            labels=labels,
            timeline=timeline,
            dispatcher=dispatcher,
            clock=clock,
        )
        self.get_label_timeline = GetLabelTimeline(
            confirmations=confirmations,
            labels=labels,
            timeline=timeline,
            accounts=accounts,
            clock=clock,
        )
        self.get_segment_label_state = GetSegmentLabelState(
            confirmations=confirmations,
            proposals=ProposalRepository(),
            timeline=timeline,
            clock=clock,
        )

    def router(self) -> APIRouter:
        return create_router(
            confirm_segment_label=self.confirm_segment_label,
            confirm_segment_labels=self.confirm_segment_labels,
            get_label_timeline=self.get_label_timeline,
            get_segment_label_state=self.get_segment_label_state,
        )


class LabelProposals:
    """Stateless label proposal use cases for the batch job."""

    def __init__(
        self,
        *,
        suggester: LabelSuggester,
        labels: LabelCatalog,
        timeline: ObservationTimeline,
        dispatcher: EventDispatcher,
        clock: Clock,
    ) -> None:
        confirmations = ConfirmationRepository()
        proposals = ProposalRepository()
        self.propose_label = ProposeLabel(
            attempts=ProposalAttempts(
                confirmations=confirmations,
                proposals=proposals,
                labels=labels,
                timeline=timeline,
            ),
            inputs=SuggestionInputs(
                labels=labels,
                examples=RecentConfirmedExampleRetriever(
                    confirmations=confirmations, timeline=timeline
                ),
            ),
            suggester=suggester,
            dispatcher=dispatcher,
            clock=clock,
        )
        self.scan_label_proposals = ScanLabelProposals(
            propose=self.propose_label,
            confirmations=confirmations,
            proposals=proposals,
            timeline=timeline,
            clock=clock,
        )
        self.sweep_label_proposals = SweepLabelProposals(scan=self.scan_label_proposals)


def create_label_proposal_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    model: str,
    labels: LabelCatalog,
    timeline: ObservationTimeline,
    dispatcher: EventDispatcher,
) -> BatchJob:
    """The label proposal job: a full sweep on start and every five minutes."""
    # Constructed up front so an invalid LABEL_MODEL fails before the first run.
    proposals = LabelProposals(
        suggester=PydanticAILabelSuggester(model),
        labels=labels,
        timeline=timeline,
        dispatcher=dispatcher,
        clock=utc_now,
    )

    async def run() -> None:
        async with session_factory() as db_session:
            await proposals.sweep_label_proposals.execute(db_session)

    return BatchJob(
        LABEL_PROPOSAL_JOB_NAME,
        run,
        every(timedelta(minutes=LABEL_PROPOSAL_INTERVAL_MINUTES), run_on_start=True),
    )
