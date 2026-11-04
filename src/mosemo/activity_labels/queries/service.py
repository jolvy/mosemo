import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activity_labels.confirmations.models import ActivityLabelConfirmation
from mosemo.activity_labels.confirmations.repository import ConfirmationRepository
from mosemo.activity_labels.confirmations.service import (
    SavedConfirmation,
    find_labelable_segment,
)
from mosemo.activity_labels.proposals.models import (
    ActivityLabelProposal,
    ActivityLabelProposalStatus,
)
from mosemo.activity_labels.proposals.repository import ProposalRepository
from mosemo.activity_labels.queries.repository import (
    ConfirmationWithLabelName,
    LabelQueryRepository,
)
from mosemo.activity_labels.schemas import (
    ActivityGroupResponse,
    ActivityLabelProposalResponse,
    ActivityLabelStateResponse,
    ConfirmedActivityLabelStateResponse,
    FailedLabelProposalResponse,
    InProgressActivityResponse,
    LabelSelectionResponse,
    LabelTimelineCaptureGapResponse,
    LabelTimelineItemResponse,
    LabelTimelineLabelSelectionResponse,
    LabelTimelineSegmentResponse,
    OpaqueActivityResponse,
    PendingActivityLabelStateResponse,
    ProcessingLabelProposalResponse,
    ReadyLabelProposalResponse,
    UnclassifiedSelectionResponse,
    WaitingLabelProposalResponse,
)
from mosemo.identity.public import AccountDirectory
from mosemo.observation.public import (
    ActivityContext,
    ActivityTimelineKind,
    ObservationTimeline,
    ObservedSegment,
)
from mosemo.shared.domain.timezones import timeline_date_window

activity_context_adapter = TypeAdapter(ActivityContext)


def group_member_version(
    *,
    segment_id: UUID,
    version: str,
    confirmation: ActivityLabelConfirmation | None,
) -> str:
    if confirmation is None:
        return f"{segment_id}:{version}:pending"
    return (
        f"{segment_id}:{version}:{confirmation.confirmation_id}:"
        f"{confirmation.updated_at.astimezone(UTC).isoformat()}:"
        f"{confirmation.label_id}"
    )


def group_version(members: list[str]) -> str:
    return sha256(json.dumps(members, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LabelTimelineEntry:
    """A labelable segment with the confirmation that applies to its version."""

    segment: ObservedSegment
    version: str
    ended_at: datetime
    confirmation: ActivityLabelConfirmation | None
    display_name: str | None

    def joins(self, previous: LabelTimelineEntry) -> bool:
        if previous.ended_at != self.segment.started_at:
            return False
        if previous.confirmation is None or self.confirmation is None:
            return previous.confirmation is None and self.confirmation is None
        return previous.confirmation.label_id == self.confirmation.label_id

    def member_response(self) -> LabelTimelineSegmentResponse:
        assert self.segment.context is not None
        assert self.segment.last_observed_at is not None
        return LabelTimelineSegmentResponse(
            segment_id=self.segment.id,
            segment_version=self.version,
            started_at=self.segment.started_at,
            ended_at=self.ended_at,
            last_observed_at=self.segment.last_observed_at,
            context=activity_context_adapter.validate_python(self.segment.context),
        )


class LabelQueries:
    def __init__(
        self,
        *,
        timeline: ObservationTimeline,
        confirmation_repository: ConfirmationRepository,
        proposal_repository: ProposalRepository,
        label_query_repository: LabelQueryRepository,
        accounts: AccountDirectory,
    ) -> None:
        self._timeline = timeline
        self._confirmation_repository = confirmation_repository
        self._proposal_repository = proposal_repository
        self._label_query_repository = label_query_repository
        self._accounts = accounts

    async def get_timeline(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        date: date | None,
        now: datetime,
    ) -> list[LabelTimelineItemResponse]:
        account_timezone = await self._accounts.get_timezone(db_session, account_id)
        window = timeline_date_window(
            account_timezone=account_timezone,
            requested_date=date,
            now=now,
        )
        if window is None:
            return []
        _, start, end = window
        visible_segments = await self._timeline.list_window(
            db_session, account_id=account_id, start=start, end=end, now=now
        )
        confirmations = (
            await self._label_query_repository.list_confirmations_with_label_names(
                account_id=account_id,
                first_event_ids={
                    segment.first_event_id
                    for segment in visible_segments
                    if segment.kind is ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY
                },
            )
        )

        results: list[LabelTimelineItemResponse] = []
        group: list[LabelTimelineEntry] = []
        for segment in visible_segments:
            if segment.kind is not ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY:
                if group:
                    results.append(self._group_response(group))
                    group = []
                results.append(self._standalone_response(segment))
                continue
            entry = self._timeline_entry(segment, confirmations)
            if group and not entry.joins(group[-1]):
                results.append(self._group_response(group))
                group = []
            group.append(entry)
        if group:
            results.append(self._group_response(group))
        return results

    @staticmethod
    def _timeline_entry(
        segment: ObservedSegment,
        confirmations: dict[UUID, ConfirmationWithLabelName],
    ) -> LabelTimelineEntry:
        ended_at = segment.ended_at
        version = segment.version
        assert ended_at is not None
        assert version is not None
        confirmation_with_name = confirmations.get(segment.first_event_id)
        if confirmation_with_name is None or not (
            confirmation_with_name.confirmation.applies_to(version)
        ):
            return LabelTimelineEntry(
                segment=segment,
                version=version,
                ended_at=ended_at,
                confirmation=None,
                display_name=None,
            )
        return LabelTimelineEntry(
            segment=segment,
            version=version,
            ended_at=ended_at,
            confirmation=confirmation_with_name.confirmation,
            display_name=confirmation_with_name.display_name,
        )

    @staticmethod
    def _standalone_response(segment: ObservedSegment) -> LabelTimelineItemResponse:
        kind = segment.kind
        ended_at = segment.ended_at
        if kind is ActivityTimelineKind.CAPTURE_GAP:
            assert segment.reason is not None
            return LabelTimelineCaptureGapResponse(
                item_type="capture_gap",
                segment_id=segment.id,
                started_at=segment.started_at,
                ended_at=ended_at,
                reason=segment.reason,
            )
        assert segment.context is not None
        assert segment.last_observed_at is not None
        context = activity_context_adapter.validate_python(segment.context)
        if kind is ActivityTimelineKind.OPAQUE_ACTIVITY:
            return OpaqueActivityResponse(
                item_type="opaque_activity",
                segment_id=segment.id,
                started_at=segment.started_at,
                ended_at=ended_at,
                last_observed_at=segment.last_observed_at,
                context=context,
            )
        assert kind is ActivityTimelineKind.IN_PROGRESS_ACTIVITY
        return InProgressActivityResponse(
            item_type="in_progress_activity",
            segment_id=segment.id,
            started_at=segment.started_at,
            ended_at=None,
            last_observed_at=segment.last_observed_at,
            context=context,
        )

    @staticmethod
    def _group_response(group: list[LabelTimelineEntry]) -> ActivityGroupResponse:
        first = group[0]
        selection: (
            UnclassifiedSelectionResponse | LabelTimelineLabelSelectionResponse | None
        )
        if first.confirmation is None:
            state = "pending"
            selection = None
        elif first.confirmation.label_id is None:
            state = "confirmed"
            selection = UnclassifiedSelectionResponse(kind="unclassified")
        else:
            assert first.display_name is not None
            state = "confirmed"
            selection = LabelTimelineLabelSelectionResponse(
                kind="label",
                label_id=first.confirmation.label_id,
                display_name=first.display_name,
            )
        return ActivityGroupResponse(
            item_type="activity_group",
            group_version=group_version(
                [
                    group_member_version(
                        segment_id=entry.segment.id,
                        version=entry.version,
                        confirmation=entry.confirmation,
                    )
                    for entry in group
                ]
            ),
            started_at=first.segment.started_at,
            ended_at=group[-1].ended_at,
            state=state,
            selection=selection,
            segments=[entry.member_response() for entry in group],
        )

    async def get_state(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        segment_id: UUID,
        now: datetime,
    ) -> ActivityLabelStateResponse:
        segment = await find_labelable_segment(
            self._timeline,
            db_session,
            account_id=account_id,
            segment_id=segment_id,
            now=now,
        )
        version = segment.version
        assert version is not None
        confirmation = await self._confirmation_repository.find_confirmation(
            account_id=account_id,
            first_event_id=segment.first_event_id,
        )
        proposal = await self._proposal_repository.find_proposal(
            account_id=account_id,
            first_event_id=segment.first_event_id,
            segment_version=version,
        )
        return self._state_response(
            segment_id=segment.id,
            version=version,
            confirmation=(
                confirmation
                if confirmation is not None and confirmation.applies_to(version)
                else None
            ),
            proposal=proposal,
            now=now,
        )

    async def confirmed_state(
        self, *, account_id: UUID, saved: SavedConfirmation, now: datetime
    ) -> ConfirmedActivityLabelStateResponse:
        proposal = await self._proposal_repository.find_proposal(
            account_id=account_id,
            first_event_id=saved.segment.first_event_id,
            segment_version=saved.version,
        )
        return self._confirmed_response(
            segment_id=saved.segment.id,
            version=saved.version,
            confirmation=saved.confirmation,
            proposal=proposal,
            now=now,
        )

    @staticmethod
    def _state_response(
        *,
        segment_id: UUID,
        version: str,
        confirmation: ActivityLabelConfirmation | None,
        proposal: ActivityLabelProposal | None,
        now: datetime,
    ) -> ActivityLabelStateResponse:
        proposal_response = LabelQueries._proposal_response(proposal, now=now)
        if confirmation is None:
            return PendingActivityLabelStateResponse(
                segment_id=segment_id,
                segment_version=version,
                state="pending",
                proposal=proposal_response,
            )
        return LabelQueries._confirmed_response(
            segment_id=segment_id,
            version=version,
            confirmation=confirmation,
            proposal=proposal,
            now=now,
        )

    @staticmethod
    def _proposal_response(
        proposal: ActivityLabelProposal | None, *, now: datetime
    ) -> ActivityLabelProposalResponse:
        if (
            proposal is None
            or proposal.status is ActivityLabelProposalStatus.SUPERSEDED
        ):
            return WaitingLabelProposalResponse(status="waiting")
        if proposal.status is ActivityLabelProposalStatus.PROCESSING:
            if proposal.has_expired_lease(now):
                return FailedLabelProposalResponse(status="failed")
            return ProcessingLabelProposalResponse(status="processing")
        if proposal.status is ActivityLabelProposalStatus.FAILED:
            return FailedLabelProposalResponse(status="failed")
        assert proposal.status is ActivityLabelProposalStatus.READY
        assert proposal.suggested_at is not None
        selection = (
            UnclassifiedSelectionResponse(kind="unclassified")
            if proposal.suggested_label_id is None
            else LabelSelectionResponse(
                kind="label", label_id=proposal.suggested_label_id
            )
        )
        return ReadyLabelProposalResponse(
            status="ready", selection=selection, suggested_at=proposal.suggested_at
        )

    @staticmethod
    def _confirmed_response(
        *,
        segment_id: UUID,
        version: str,
        confirmation: ActivityLabelConfirmation,
        proposal: ActivityLabelProposal | None,
        now: datetime,
    ) -> ConfirmedActivityLabelStateResponse:
        if confirmation.label_id is None:
            selection = UnclassifiedSelectionResponse(kind="unclassified")
        else:
            selection = LabelSelectionResponse(
                kind="label",
                label_id=confirmation.label_id,
            )
        return ConfirmedActivityLabelStateResponse(
            segment_id=segment_id,
            segment_version=version,
            state="confirmed",
            selection=selection,
            confirmed_at=confirmation.confirmed_at,
            updated_at=confirmation.updated_at,
            proposal=(
                LabelQueries._proposal_response(proposal, now=now)
                if proposal is not None
                and proposal.status is ActivityLabelProposalStatus.READY
                else None
            ),
        )
