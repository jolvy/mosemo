from uuid import UUID

from pydantic import TypeAdapter

from mosemo.labeling.application.confirmations import LabelSelection
from mosemo.labeling.application.states import (
    ProposalProgress,
    ProposalState,
    SegmentLabelState,
)
from mosemo.labeling.application.timeline import (
    LabelTimelineEntry,
    LabelTimelineGroup,
    LabelTimelineItem,
)
from mosemo.labeling.presentation.schemas import (
    ActivityGroupResponse,
    ActivityLabelConfirmationRequest,
    ActivityLabelProposalResponse,
    ActivityLabelStateResponse,
    BatchLabelConfirmationRequest,
    ConfirmedActivityLabelStateResponse,
    FailedLabelProposalResponse,
    InProgressActivityResponse,
    LabelSelectionRequest,
    LabelSelectionResponse,
    LabelTimelineCaptureGapResponse,
    LabelTimelineItemResponse,
    LabelTimelineLabelSelectionResponse,
    LabelTimelineSegmentResponse,
    OpaqueActivityResponse,
    PendingActivityLabelStateResponse,
    ProcessingLabelProposalResponse,
    ReadyLabelProposalResponse,
    UnclassifiedSelectionRequest,
    UnclassifiedSelectionResponse,
    WaitingLabelProposalResponse,
)
from mosemo.observation.public import (
    ActivityContext,
    ActivityTimelineKind,
    ObservedSegment,
)

activity_context_adapter = TypeAdapter(ActivityContext)


def _label_id(
    selection: LabelSelectionRequest | UnclassifiedSelectionRequest,
) -> UUID | None:
    return selection.label_id if isinstance(selection, LabelSelectionRequest) else None


class LabelSelectionMapper:
    def to_selection(
        self, request: ActivityLabelConfirmationRequest, *, segment_id: UUID
    ) -> LabelSelection:
        return LabelSelection(
            segment_id=segment_id,
            segment_version=request.segment_version,
            label_id=_label_id(request.selection),
        )

    def to_selections(
        self, request: BatchLabelConfirmationRequest
    ) -> list[LabelSelection]:
        return [
            LabelSelection(
                segment_id=item.segment_id,
                segment_version=item.segment_version,
                label_id=_label_id(item.selection),
            )
            for item in request.items
        ]


class LabelStateMapper:
    def to_response(self, state: SegmentLabelState) -> ActivityLabelStateResponse:
        if state.confirmation is None:
            return PendingActivityLabelStateResponse(
                segment_id=state.segment_id,
                segment_version=state.segment_version,
                state="pending",
                proposal=self._proposal_response(state.proposal),
            )
        return self.to_confirmed_response(state)

    def to_confirmed_response(
        self, state: SegmentLabelState
    ) -> ConfirmedActivityLabelStateResponse:
        confirmation = state.confirmation
        assert confirmation is not None
        return ConfirmedActivityLabelStateResponse(
            segment_id=state.segment_id,
            segment_version=state.segment_version,
            state="confirmed",
            selection=(
                UnclassifiedSelectionResponse(kind="unclassified")
                if confirmation.label_id is None
                else LabelSelectionResponse(
                    kind="label", label_id=confirmation.label_id
                )
            ),
            confirmed_at=confirmation.confirmed_at,
            updated_at=confirmation.updated_at,
            proposal=(
                self._proposal_response(state.proposal)
                if state.proposal.progress is ProposalProgress.READY
                else None
            ),
        )

    @staticmethod
    def _proposal_response(proposal: ProposalState) -> ActivityLabelProposalResponse:
        match proposal.progress:
            case ProposalProgress.WAITING:
                return WaitingLabelProposalResponse(status="waiting")
            case ProposalProgress.PROCESSING:
                return ProcessingLabelProposalResponse(status="processing")
            case ProposalProgress.FAILED:
                return FailedLabelProposalResponse(status="failed")
            case ProposalProgress.READY:
                assert proposal.suggested_at is not None
                return ReadyLabelProposalResponse(
                    status="ready",
                    selection=(
                        UnclassifiedSelectionResponse(kind="unclassified")
                        if proposal.label_id is None
                        else LabelSelectionResponse(
                            kind="label", label_id=proposal.label_id
                        )
                    ),
                    suggested_at=proposal.suggested_at,
                )


class LabelTimelineMapper:
    def to_response(self, item: LabelTimelineItem) -> LabelTimelineItemResponse:
        if isinstance(item, LabelTimelineGroup):
            return self._group_response(item)
        return self._standalone_response(item)

    @staticmethod
    def _member_response(entry: LabelTimelineEntry) -> LabelTimelineSegmentResponse:
        segment = entry.segment
        assert segment.context is not None
        assert segment.last_observed_at is not None
        return LabelTimelineSegmentResponse(
            id=segment.id,
            segment_version=entry.version,
            started_at=segment.started_at,
            ended_at=entry.ended_at,
            last_observed_at=segment.last_observed_at,
            context=activity_context_adapter.validate_python(segment.context),
        )

    def _group_response(self, group: LabelTimelineGroup) -> ActivityGroupResponse:
        first = group.entries[0]
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
            group_version=group.version,
            started_at=first.segment.started_at,
            ended_at=group.entries[-1].ended_at,
            state=state,
            selection=selection,
            segments=[self._member_response(entry) for entry in group.entries],
        )

    @staticmethod
    def _standalone_response(segment: ObservedSegment) -> LabelTimelineItemResponse:
        kind = segment.kind
        ended_at = segment.ended_at
        if kind is ActivityTimelineKind.CAPTURE_GAP:
            assert segment.reason is not None
            return LabelTimelineCaptureGapResponse(
                item_type="capture_gap",
                id=segment.id,
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
                id=segment.id,
                started_at=segment.started_at,
                ended_at=ended_at,
                last_observed_at=segment.last_observed_at,
                context=context,
            )
        assert kind is ActivityTimelineKind.IN_PROGRESS_ACTIVITY
        return InProgressActivityResponse(
            item_type="in_progress_activity",
            id=segment.id,
            started_at=segment.started_at,
            ended_at=None,
            last_observed_at=segment.last_observed_at,
            context=context,
        )
