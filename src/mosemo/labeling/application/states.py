from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.infra.transactions import read_snapshot
from mosemo.labeling.domain.confirmations import (
    ActivityLabelConfirmation,
    ActivityLabelSegmentNotFoundError,
    ActivityLabelSegmentNotLabelableError,
)
from mosemo.labeling.domain.proposals import (
    ActivityLabelProposal,
    ActivityLabelProposalStatus,
)
from mosemo.labeling.infrastructure.confirmations import ConfirmationRepository
from mosemo.labeling.infrastructure.proposals import ProposalRepository
from mosemo.observation.public import (
    ActivityTimelineKind,
    ObservationTimeline,
    ObservedSegment,
)
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.use_case import UseCase


class ProposalProgress(StrEnum):
    WAITING = "waiting"
    PROCESSING = "processing"
    FAILED = "failed"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class ProposalState:
    """The AI proposal as the user sees it; `label_id` `None` is unclassified."""

    progress: ProposalProgress
    label_id: UUID | None = None
    suggested_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SegmentLabelState:
    """A labelable segment with the confirmation that applies to its version."""

    segment_id: UUID
    segment_version: str
    confirmation: ActivityLabelConfirmation | None
    proposal: ProposalState


def proposal_state(
    proposal: ActivityLabelProposal | None, *, now: datetime
) -> ProposalState:
    """A superseded or missing proposal is waiting; an expired lease has failed."""
    if proposal is None or proposal.status is ActivityLabelProposalStatus.SUPERSEDED:
        return ProposalState(ProposalProgress.WAITING)
    if proposal.status is ActivityLabelProposalStatus.PROCESSING:
        if proposal.has_expired_lease(now):
            return ProposalState(ProposalProgress.FAILED)
        return ProposalState(ProposalProgress.PROCESSING)
    if proposal.status is ActivityLabelProposalStatus.FAILED:
        return ProposalState(ProposalProgress.FAILED)
    assert proposal.status is ActivityLabelProposalStatus.READY
    assert proposal.suggested_at is not None
    return ProposalState(
        ProposalProgress.READY,
        label_id=proposal.suggested_label_id,
        suggested_at=proposal.suggested_at,
    )


async def find_labelable_segment(
    timeline: ObservationTimeline,
    db_session: AsyncSession,
    *,
    account_id: UUID,
    segment_id: UUID,
    now: datetime,
) -> ObservedSegment:
    """The account's closed detailed segment with this ID.

    Raises `ActivityLabelSegmentNotFoundError` or
    `ActivityLabelSegmentNotLabelableError`.
    """
    segment = await timeline.find_segment(
        db_session, account_id=account_id, segment_id=segment_id, now=now
    )
    if segment is None:
        raise ActivityLabelSegmentNotFoundError
    if segment.kind is not ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY:
        raise ActivityLabelSegmentNotLabelableError
    return segment


class GetSegmentLabelState(UseCase):
    def __init__(
        self,
        *,
        confirmations: ConfirmationRepository,
        proposals: ProposalRepository,
        timeline: ObservationTimeline,
        clock: Clock,
    ) -> None:
        self._confirmations = confirmations
        self._proposals = proposals
        self._timeline = timeline
        self._clock = clock

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, segment_id: UUID
    ) -> SegmentLabelState:
        """Raises `ActivityLabelSegmentNotFoundError` or
        `ActivityLabelSegmentNotLabelableError`."""
        now = self._clock()
        async with read_snapshot(db_session):
            segment = await find_labelable_segment(
                self._timeline,
                db_session,
                account_id=account_id,
                segment_id=segment_id,
                now=now,
            )
            version = segment.version
            assert version is not None
            confirmation = await self._confirmations.find(
                db_session,
                account_id=account_id,
                first_event_id=segment.first_event_id,
            )
            proposal = await self._proposals.find(
                db_session,
                account_id=account_id,
                first_event_id=segment.first_event_id,
                segment_version=version,
            )
        return SegmentLabelState(
            segment_id=segment.id,
            segment_version=version,
            confirmation=(
                confirmation
                if confirmation is not None and confirmation.applies_to(version)
                else None
            ),
            proposal=proposal_state(proposal, now=now),
        )
