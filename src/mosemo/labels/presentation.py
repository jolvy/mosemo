"""Convert label domain state into public API response models."""

from datetime import datetime
from uuid import UUID

from pydantic import TypeAdapter

from mosemo.activities.schemas import ActivityContext
from mosemo.labels.confirmation import SavedConfirmation
from mosemo.labels.models import (
    ActivityLabelConfirmation,
    ActivityLabelProposal,
    ActivityLabelProposalStatus,
)
from mosemo.labels.schemas import (
    ActivityGroupResponse,
    ActivityLabelProposalResponse,
    ActivityLabelStateResponse,
    BatchLabelConfirmationResponse,
    ConfirmedActivityLabelStateResponse,
    FailedLabelProposalResponse,
    InProgressActivityResponse,
    LabelGroupConfirmationResult,
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
from mosemo.labels.timeline import (
    ActivityGroup,
    CaptureGap,
    InProgressActivity,
    LabelTimeline,
    OpaqueActivity,
    TimelineItem,
)

activity_context_adapter = TypeAdapter(ActivityContext)


def timeline_response(timeline: LabelTimeline) -> list[LabelTimelineItemResponse]:
    return [_timeline_item_response(item) for item in timeline.items]


def _timeline_item_response(item: TimelineItem) -> LabelTimelineItemResponse:
    if isinstance(item, ActivityGroup):
        return _group_response(item)
    if isinstance(item, CaptureGap):
        return LabelTimelineCaptureGapResponse(
            item_type="capture_gap",
            segment_id=item.segment_id,
            started_at=item.started_at,
            ended_at=item.ended_at,
            reason=item.reason,
        )
    context = activity_context_adapter.validate_python(item.context)
    if isinstance(item, OpaqueActivity):
        return OpaqueActivityResponse(
            item_type="opaque_activity",
            segment_id=item.segment_id,
            started_at=item.started_at,
            ended_at=item.ended_at,
            last_observed_at=item.last_observed_at,
            context=context,
        )
    assert isinstance(item, InProgressActivity)
    return InProgressActivityResponse(
        item_type="in_progress_activity",
        segment_id=item.segment_id,
        started_at=item.started_at,
        ended_at=None,
        last_observed_at=item.last_observed_at,
        context=context,
    )


def _group_response(group: ActivityGroup) -> ActivityGroupResponse:
    confirmation = group.confirmation
    if confirmation is None:
        selection = None
        state = "pending"
    elif confirmation.label_id is None:
        selection = UnclassifiedSelectionResponse(kind="unclassified")
        state = "confirmed"
    else:
        assert confirmation.display_name is not None
        selection = LabelTimelineLabelSelectionResponse(
            kind="label",
            label_id=confirmation.label_id,
            display_name=confirmation.display_name,
        )
        state = "confirmed"
    return ActivityGroupResponse(
        item_type="activity_group",
        group_version=group.version,
        started_at=group.started_at,
        ended_at=group.ended_at,
        state=state,
        selection=selection,
        segments=[
            LabelTimelineSegmentResponse(
                segment_id=member.segment_id,
                segment_version=member.segment_version,
                started_at=member.started_at,
                ended_at=member.ended_at,
                last_observed_at=member.last_observed_at,
                context=activity_context_adapter.validate_python(member.context),
            )
            for member in group.members
        ],
    )


def state_response(
    *,
    segment_id: UUID,
    version: str,
    confirmation: ActivityLabelConfirmation | None,
    proposal: ActivityLabelProposal | None,
    now: datetime,
) -> ActivityLabelStateResponse:
    if confirmation is None:
        return PendingActivityLabelStateResponse(
            segment_id=segment_id,
            segment_version=version,
            state="pending",
            proposal=_proposal_response(proposal, now=now),
        )
    return confirmed_response(
        SavedConfirmation(segment_id, version, confirmation, proposal), now=now
    )


def batch_response(
    groups: list[list[SavedConfirmation]], *, now: datetime
) -> BatchLabelConfirmationResponse:
    return BatchLabelConfirmationResponse(
        items=[
            LabelGroupConfirmationResult(
                segments=[confirmed_response(result, now=now) for result in group]
            )
            for group in groups
        ]
    )


def confirmed_response(
    result: SavedConfirmation, *, now: datetime
) -> ConfirmedActivityLabelStateResponse:
    confirmation = result.confirmation
    if confirmation.label_id is None:
        selection = UnclassifiedSelectionResponse(kind="unclassified")
    else:
        selection = LabelSelectionResponse(kind="label", label_id=confirmation.label_id)
    proposal = result.proposal
    return ConfirmedActivityLabelStateResponse(
        segment_id=result.segment_id,
        segment_version=result.segment_version,
        state="confirmed",
        selection=selection,
        confirmed_at=confirmation.confirmed_at,
        updated_at=confirmation.updated_at,
        proposal=(
            _proposal_response(proposal, now=now)
            if proposal is not None
            and proposal.status is ActivityLabelProposalStatus.READY
            else None
        ),
    )


def _proposal_response(
    proposal: ActivityLabelProposal | None, *, now: datetime
) -> ActivityLabelProposalResponse:
    if proposal is None or proposal.status is ActivityLabelProposalStatus.SUPERSEDED:
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
        else LabelSelectionResponse(kind="label", label_id=proposal.suggested_label_id)
    )
    return ReadyLabelProposalResponse(
        status="ready", selection=selection, suggested_at=proposal.suggested_at
    )
