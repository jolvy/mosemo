import json
from collections.abc import Callable
from dataclasses import dataclass
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
    BatchLabelConfirmationRequest,
    BatchLabelConfirmationResponse,
    ConfirmedActivityLabelStateResponse,
    FailedLabelProposalResponse,
    InProgressActivityResponse,
    LabelGroupConfirmationRequest,
    LabelGroupConfirmationResult,
    LabelGroupSegmentRequest,
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


class BatchLabelConfirmationFailure(Exception):
    def __init__(
        self,
        *,
        item_index: int,
        reason: Exception,
        segment_index: int | None = None,
    ) -> None:
        super().__init__(str(reason))
        self.item_index = item_index
        self.segment_index = segment_index
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ConfirmationTarget:
    segment: ActivityTimelineSegment
    version: str
    label_id: UUID | None

    def matches(self, confirmation: ActivityLabelConfirmation | None) -> bool:
        return (
            confirmation is not None
            and confirmation.segment_version == self.version
            and confirmation.label_id == self.label_id
        )


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
            return await self._timeline_in_transaction(
                account_id=account_id, date=date, current_time=current_time
            )

    async def _timeline_in_transaction(
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
            await self._label_repository.list_confirmations_with_label_names(
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

            return await self._save_confirmation(
                account_id=account_id,
                target=ConfirmationTarget(
                    segment=segment, version=version, label_id=label_id
                ),
                current_time=current_time,
            )

    async def confirm_batch(
        self,
        *,
        account_id: UUID,
        request: BatchLabelConfirmationRequest,
        now: datetime | None = None,
    ) -> BatchLabelConfirmationResponse:
        current_time = now or datetime.now(UTC)
        async with self._session.begin():
            await acquire_activity_timeline_lock(self._session, account_id=account_id)
            groups = await self._validate_batch_groups(
                account_id=account_id, request=request, current_time=current_time
            )
            results = []
            for targets in groups:
                confirmed = [
                    await self._save_confirmation(
                        account_id=account_id,
                        target=target,
                        current_time=current_time,
                    )
                    for target in targets
                ]
                results.append(LabelGroupConfirmationResult(segments=confirmed))
            return BatchLabelConfirmationResponse(items=results)

    async def _validate_batch_groups(
        self,
        *,
        account_id: UUID,
        request: BatchLabelConfirmationRequest,
        current_time: datetime,
    ) -> list[list[ConfirmationTarget]]:
        timelines: dict[date, list[LabelTimelineItemResponse]] = {}
        groups = []
        for item_index, item in enumerate(request.items):
            targets = await self._validate_group_members(
                account_id=account_id,
                item=item,
                item_index=item_index,
                current_time=current_time,
            )
            if not await self._already_confirmed(account_id, targets):
                if item.date not in timelines:
                    timelines[item.date] = await self._timeline_in_transaction(
                        account_id=account_id,
                        date=item.date,
                        current_time=current_time,
                    )
                if not self._matches_current_group(item, timelines[item.date]):
                    raise BatchLabelConfirmationFailure(
                        item_index=item_index,
                        reason=ActivityLabelSegmentChangedError(),
                    )
            groups.append(targets)
        return groups

    async def _validate_group_members(
        self,
        *,
        account_id: UUID,
        item: LabelGroupConfirmationRequest,
        item_index: int,
        current_time: datetime,
    ) -> list[ConfirmationTarget]:
        label_id = getattr(item.selection, "label_id", None)
        if label_id is not None:
            label = await self._label_repository.find_active_owned(
                account_id=account_id, label_id=label_id
            )
            if label is None:
                raise BatchLabelConfirmationFailure(
                    item_index=item_index, reason=ActivityLabelUnavailableError()
                )
        return [
            await self._validate_batch_member(
                account_id=account_id,
                member=member,
                label_id=label_id,
                item_index=item_index,
                segment_index=segment_index,
                current_time=current_time,
            )
            for segment_index, member in enumerate(item.segments)
        ]

    async def _validate_batch_member(
        self,
        *,
        account_id: UUID,
        member: LabelGroupSegmentRequest,
        label_id: UUID | None,
        item_index: int,
        segment_index: int,
        current_time: datetime,
    ) -> ConfirmationTarget:
        try:
            segment = await self._find_labelable_segment(
                account_id=account_id,
                segment_id=member.segment_id,
                current_time=current_time,
            )
        except (
            ActivityLabelSegmentNotFoundError,
            ActivityLabelSegmentNotLabelableError,
        ) as exc:
            raise BatchLabelConfirmationFailure(
                item_index=item_index, segment_index=segment_index, reason=exc
            ) from exc
        ended_at = segment.effective_ended_at(current_time)
        assert ended_at is not None
        version = segment_version(segment, ended_at=ended_at)
        if member.segment_version != version:
            raise BatchLabelConfirmationFailure(
                item_index=item_index,
                segment_index=segment_index,
                reason=ActivityLabelSegmentChangedError(),
            )
        return ConfirmationTarget(segment=segment, version=version, label_id=label_id)

    async def _already_confirmed(
        self, account_id: UUID, targets: list[ConfirmationTarget]
    ) -> bool:
        for target in targets:
            confirmation = await self._label_repository.find_confirmation(
                account_id=account_id, first_event_id=target.segment.first_event_id
            )
            if not target.matches(confirmation):
                return False
        return True

    @staticmethod
    def _matches_current_group(
        requested: LabelGroupConfirmationRequest,
        timeline: list[LabelTimelineItemResponse],
    ) -> bool:
        members = [
            (member.segment_id, member.segment_version) for member in requested.segments
        ]
        return any(
            [(member.segment_id, member.segment_version) for member in item.segments]
            == members
            and item.group_version == requested.group_version
            for item in timeline
            if isinstance(item, ActivityGroupResponse)
        )

    async def _save_confirmation(
        self,
        *,
        account_id: UUID,
        target: ConfirmationTarget,
        current_time: datetime,
    ) -> ConfirmedActivityLabelStateResponse:
        segment = target.segment
        confirmation = await self._label_repository.find_confirmation(
            account_id=account_id, first_event_id=segment.first_event_id
        )
        if confirmation is None:
            confirmation = ActivityLabelConfirmation(
                account_id=account_id,
                first_event_id=segment.first_event_id,
                segment_version=target.version,
                label_id=target.label_id,
                confirmed_at=current_time,
                updated_at=current_time,
            )
            self._session.add(confirmation)
        else:
            confirmation.apply_selection(
                segment_version=target.version,
                label_id=target.label_id,
                now=current_time,
            )

        await self._session.flush()
        proposal = await self._label_repository.find_proposal(
            account_id=account_id,
            first_event_id=segment.first_event_id,
            segment_version=target.version,
        )
        return self._confirmed_response(
            segment_id=segment.segment_id,
            version=target.version,
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
