import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.enums import ActivityTimelineKind
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.schemas import (
    ActivityContext,
)
from mosemo.activities.service import (
    ActivityAccountNotFoundError,
    acquire_activity_timeline_lock,
    timeline_date_window,
)
from mosemo.labels.models import (
    ActivityLabelConfirmation,
    ActivityLabelProposal,
    ActivityLabelProposalStatus,
)
from mosemo.labels.repository import LabelRepository
from mosemo.labels.schemas import (
    ActivityGroupResponse,
    ActivityLabelConfirmationRequest,
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


class ActivityLabelSegmentNotFoundError(Exception):
    pass


class ActivityLabelSegmentNotLabelableError(Exception):
    pass


class ActivityLabelSegmentChangedError(Exception):
    pass


class ActivityLabelUnavailableError(Exception):
    pass


activity_context_adapter = TypeAdapter(ActivityContext)


def segment_version(
    segment: ActivityTimelineSegment,
    *,
    ended_at: datetime,
) -> str:
    assert segment.last_event_id is not None
    assert segment.last_observed_at is not None
    assert segment.context is not None
    canonical = json.dumps(
        {
            "context": segment.context,
            "endedAt": ended_at.astimezone(UTC).isoformat(),
            "firstEventId": str(segment.first_event_id),
            "lastEventId": str(segment.last_event_id),
            "lastObservedAt": segment.last_observed_at.astimezone(UTC).isoformat(),
            "startedAt": segment.started_at.astimezone(UTC).isoformat(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class ActivityLabelService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        activity_repository: ActivityRepository,
        label_repository: LabelRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._session = session
        self._activity_repository = activity_repository
        self._label_repository = label_repository
        self._clock = clock

    async def get_timeline(
        self,
        *,
        account_id: UUID,
        date: date | None = None,
        now: datetime | None = None,
    ) -> list[LabelTimelineItemResponse]:
        current_time = now or self._clock()
        async with self._session.begin():
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
                await self._label_repository.list_confirmations_with_label_names(
                    account_id=account_id,
                    first_event_ids=labelable_first_event_ids,
                )
            )

            results: list[LabelTimelineItemResponse] = []
            current_group: ActivityGroupResponse | None = None

            def flush_group() -> None:
                nonlocal current_group
                if current_group is not None:
                    results.append(current_group)
                    current_group = None

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
                    confirmation_with_name[0]
                    if confirmation_with_name is not None
                    and confirmation_with_name[0].segment_version == version
                    else None
                )
                display_name = (
                    confirmation_with_name[1]
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
                if (
                    current_group is not None
                    and current_group.ended_at == segment.started_at
                    and current_group.state == state
                    and current_group.selection == selection
                ):
                    current_group.segments.append(member)
                    current_group.ended_at = ended_at
                else:
                    flush_group()
                    current_group = ActivityGroupResponse(
                        item_type="activity_group",
                        started_at=segment.started_at,
                        ended_at=ended_at,
                        state=state,
                        selection=selection,
                        segments=[member],
                    )

            flush_group()
            return results

    async def get_state(
        self,
        *,
        account_id: UUID,
        segment_id: UUID,
        now: datetime | None = None,
    ) -> ActivityLabelStateResponse:
        current_time = now or datetime.now(UTC)
        async with self._session.begin():
            segment = await self._find_labelable_segment(
                account_id=account_id,
                segment_id=segment_id,
                current_time=current_time,
            )
            ended_at = segment.effective_ended_at(current_time)
            assert ended_at is not None
            version = segment_version(segment, ended_at=ended_at)
            confirmation = await self._label_repository.find_confirmation(
                account_id=account_id,
                first_event_id=segment.first_event_id,
            )
            proposal = await self._label_repository.find_proposal(
                account_id=account_id,
                first_event_id=segment.first_event_id,
                segment_version=version,
            )
            return self._state_response(
                segment_id=segment.segment_id,
                version=version,
                confirmation=(
                    confirmation
                    if confirmation is not None
                    and confirmation.segment_version == version
                    else None
                ),
                proposal=proposal,
                now=current_time,
            )

    async def confirm(
        self,
        *,
        account_id: UUID,
        segment_id: UUID,
        request: ActivityLabelConfirmationRequest,
        now: datetime | None = None,
    ) -> ConfirmedActivityLabelStateResponse:
        current_time = now or datetime.now(UTC)
        async with self._session.begin():
            await acquire_activity_timeline_lock(
                self._session,
                account_id=account_id,
            )
            segment = await self._find_labelable_segment(
                account_id=account_id,
                segment_id=segment_id,
                current_time=current_time,
            )
            ended_at = segment.effective_ended_at(current_time)
            assert ended_at is not None
            version = segment_version(segment, ended_at=ended_at)
            if request.segment_version != version:
                raise ActivityLabelSegmentChangedError

            label_id = getattr(request.selection, "label_id", None)
            if label_id is not None:
                label = await self._label_repository.find_active_owned(
                    account_id=account_id,
                    label_id=label_id,
                )
                if label is None:
                    raise ActivityLabelUnavailableError

            confirmation = await self._label_repository.find_confirmation(
                account_id=account_id,
                first_event_id=segment.first_event_id,
            )
            if confirmation is None:
                confirmation = ActivityLabelConfirmation(
                    account_id=account_id,
                    first_event_id=segment.first_event_id,
                    segment_version=version,
                    label_id=label_id,
                    confirmed_at=current_time,
                    updated_at=current_time,
                )
                self._session.add(confirmation)
            elif confirmation.segment_version != version:
                confirmation.segment_version = version
                confirmation.label_id = label_id
                confirmation.confirmed_at = current_time
                confirmation.updated_at = current_time
            elif confirmation.label_id != label_id:
                confirmation.label_id = label_id
                confirmation.updated_at = current_time

            await self._session.flush()
            proposal = await self._label_repository.find_proposal(
                account_id=account_id,
                first_event_id=segment.first_event_id,
                segment_version=version,
            )
            return self._confirmed_response(
                segment_id=segment.segment_id,
                version=version,
                confirmation=confirmation,
                proposal=proposal,
                now=current_time,
            )

    async def _find_labelable_segment(
        self,
        *,
        account_id: UUID,
        segment_id: UUID,
        current_time: datetime,
    ) -> ActivityTimelineSegment:
        segment = await self._activity_repository.find_account_segment(
            account_id=account_id,
            segment_id=segment_id,
        )
        if segment is None:
            raise ActivityLabelSegmentNotFoundError
        if (
            segment.timeline_kind_at(current_time)
            is not ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY
        ):
            raise ActivityLabelSegmentNotLabelableError
        return segment

    @staticmethod
    def _state_response(
        *,
        segment_id: UUID,
        version: str,
        confirmation: ActivityLabelConfirmation | None,
        proposal: ActivityLabelProposal | None,
        now: datetime,
    ) -> ActivityLabelStateResponse:
        proposal_response = ActivityLabelService._proposal_response(proposal, now=now)
        if confirmation is None:
            return PendingActivityLabelStateResponse(
                segment_id=segment_id,
                segment_version=version,
                state="pending",
                proposal=proposal_response,
            )
        return ActivityLabelService._confirmed_response(
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
                ActivityLabelService._proposal_response(proposal, now=now)
                if proposal is not None
                and proposal.status is ActivityLabelProposalStatus.READY
                else None
            ),
        )
