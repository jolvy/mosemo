import json
from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import UUID

from pydantic import TypeAdapter

from mosemo.activities.enums import ActivityTimelineKind
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.schemas import ActivityContext
from mosemo.activities.service import ActivityAccountNotFoundError, timeline_date_window
from mosemo.activities.versions import segment_version
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
from mosemo.activity_labels.queries.repository import LabelQueryRepository
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


class LabelQueries:
    def __init__(
        self,
        *,
        activity_repository: ActivityRepository,
        confirmation_repository: ConfirmationRepository,
        proposal_repository: ProposalRepository,
        label_query_repository: LabelQueryRepository,
    ) -> None:
        self._activity_repository = activity_repository
        self._confirmation_repository = confirmation_repository
        self._proposal_repository = proposal_repository
        self._label_query_repository = label_query_repository

    async def get_timeline(
        self, *, account_id: UUID, date: date | None, current_time: datetime
    ) -> list[LabelTimelineItemResponse]:
        account_timezone = await self._activity_repository.find_account_timezone(
            account_id
        )
        if account_timezone is None:
            raise ActivityAccountNotFoundError
        window = timeline_date_window(
            account_timezone=account_timezone,
            requested_date=date,
            current_time=current_time,
        )
        if window is None:
            return []
        _, start, end = window
        segments = await self._activity_repository.list_date_segments(
            account_id=account_id,
            start=start,
            end=end,
        )

        labelable_first_event_ids: set[UUID] = set()
        for segment in segments:
            if not segment.overlaps_window(
                start=start,
                end=end,
                now=current_time,
            ):
                continue
            if (
                segment.timeline_kind_at(current_time)
                is ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY
            ):
                labelable_first_event_ids.add(segment.first_event_id)

        confirmations = (
            await self._label_query_repository.list_confirmations_with_label_names(
                account_id=account_id,
                first_event_ids=labelable_first_event_ids,
            )
        )

        results: list[LabelTimelineItemResponse] = []
        current_group: ActivityGroupResponse | None = None
        current_group_versions: list[str] = []

        def flush_group() -> None:
            nonlocal current_group, current_group_versions
            if current_group is not None:
                results.append(current_group)
                current_group = None
                current_group_versions = []

        for segment in segments:
            if not segment.overlaps_window(
                start=start,
                end=end,
                now=current_time,
            ):
                continue

            kind = segment.timeline_kind_at(current_time)
            ended_at = segment.effective_ended_at(current_time)
            if kind is ActivityTimelineKind.CAPTURE_GAP:
                flush_group()
                assert segment.reason is not None
                results.append(
                    LabelTimelineCaptureGapResponse(
                        item_type="capture_gap",
                        segment_id=segment.segment_id,
                        started_at=segment.started_at,
                        ended_at=ended_at,
                        reason=segment.reason,
                    )
                )
                continue

            assert segment.context is not None
            context = activity_context_adapter.validate_python(segment.context)
            if kind is ActivityTimelineKind.OPAQUE_ACTIVITY:
                flush_group()
                assert segment.last_observed_at is not None
                results.append(
                    OpaqueActivityResponse(
                        item_type="opaque_activity",
                        segment_id=segment.segment_id,
                        started_at=segment.started_at,
                        ended_at=ended_at,
                        last_observed_at=segment.last_observed_at,
                        context=context,
                    )
                )
                continue

            assert segment.last_observed_at is not None
            if kind is ActivityTimelineKind.IN_PROGRESS_ACTIVITY:
                flush_group()
                results.append(
                    InProgressActivityResponse(
                        item_type="in_progress_activity",
                        segment_id=segment.segment_id,
                        started_at=segment.started_at,
                        ended_at=None,
                        last_observed_at=segment.last_observed_at,
                        context=context,
                    )
                )
                continue

            assert ended_at is not None
            version = segment_version(segment, ended_at=ended_at)
            confirmation_with_name = confirmations.get(segment.first_event_id)
            confirmation = (
                confirmation_with_name.confirmation
                if confirmation_with_name is not None
                and confirmation_with_name.confirmation.segment_version == version
                else None
            )
            display_name = (
                confirmation_with_name.display_name
                if confirmation is not None and confirmation_with_name is not None
                else None
            )
            if confirmation is None:
                state = "pending"
                selection = None
            elif confirmation.label_id is None:
                state = "confirmed"
                selection = UnclassifiedSelectionResponse(kind="unclassified")
            else:
                assert display_name is not None
                state = "confirmed"
                selection = LabelTimelineLabelSelectionResponse(
                    kind="label",
                    label_id=confirmation.label_id,
                    display_name=display_name,
                )

            member = LabelTimelineSegmentResponse(
                segment_id=segment.segment_id,
                segment_version=version,
                started_at=segment.started_at,
                ended_at=ended_at,
                last_observed_at=segment.last_observed_at,
                context=context,
            )
            member_version = group_member_version(
                segment_id=segment.segment_id,
                version=version,
                confirmation=confirmation,
            )
            if (
                current_group is not None
                and current_group.ended_at == segment.started_at
                and current_group.state == state
                and current_group.selection == selection
            ):
                current_group.segments.append(member)
                current_group.ended_at = ended_at
                current_group_versions.append(member_version)
                current_group.group_version = group_version(current_group_versions)
            else:
                flush_group()
                current_group_versions = [member_version]
                current_group = ActivityGroupResponse(
                    item_type="activity_group",
                    group_version=group_version(current_group_versions),
                    started_at=segment.started_at,
                    ended_at=ended_at,
                    state=state,
                    selection=selection,
                    segments=[member],
                )

        flush_group()
        return results

    async def get_state(
        self, *, account_id: UUID, segment_id: UUID, current_time: datetime
    ) -> ActivityLabelStateResponse:
        segment = await find_labelable_segment(
            self._activity_repository,
            account_id=account_id,
            segment_id=segment_id,
            current_time=current_time,
        )
        ended_at = segment.effective_ended_at(current_time)
        assert ended_at is not None
        version = segment_version(segment, ended_at=ended_at)
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
            segment_id=segment.segment_id,
            version=version,
            confirmation=(
                confirmation
                if confirmation is not None and confirmation.segment_version == version
                else None
            ),
            proposal=proposal,
            now=current_time,
        )

    async def confirmed_state(
        self, *, account_id: UUID, saved: SavedConfirmation, current_time: datetime
    ) -> ConfirmedActivityLabelStateResponse:
        proposal = await self._proposal_repository.find_proposal(
            account_id=account_id,
            first_event_id=saved.segment.first_event_id,
            segment_version=saved.version,
        )
        return self._confirmed_response(
            segment_id=saved.segment.segment_id,
            version=saved.version,
            confirmation=saved.confirmation,
            proposal=proposal,
            now=current_time,
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
