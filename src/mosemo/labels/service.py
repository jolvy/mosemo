from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.enums import ActivityTimelineKind
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.service import (
    ActivityAccountNotFoundError,
    acquire_activity_timeline_lock,
    timeline_date_window,
)
from mosemo.labels.confirmation import ConfirmationTarget, SavedConfirmation
from mosemo.labels.models import ActivityLabelConfirmation
from mosemo.labels.presentation import (
    batch_response,
    confirmed_response,
    state_response,
    timeline_response,
)
from mosemo.labels.repository import LabelRepository
from mosemo.labels.schemas import (
    ActivityLabelConfirmationRequest,
    ActivityLabelStateResponse,
    BatchLabelConfirmationRequest,
    BatchLabelConfirmationResponse,
    ConfirmedActivityLabelStateResponse,
    LabelGroupConfirmationRequest,
    LabelGroupSegmentRequest,
    LabelTimelineItemResponse,
)
from mosemo.labels.timeline import GroupSnapshot, LabelTimeline, SegmentSnapshot
from mosemo.labels.timeline_projection import build_label_timeline
from mosemo.labels.versions import segment_version


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
            timeline = await self._timeline_in_transaction(
                account_id=account_id, date=date, current_time=current_time
            )
        return timeline_response(timeline)

    async def _timeline_in_transaction(
        self, *, account_id: UUID, date: date | None, current_time: datetime
    ) -> LabelTimeline:
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
            return LabelTimeline()
        _, start, end = window
        segments = await self._activity_repository.list_date_segments(
            account_id=account_id,
            start=start,
            end=end,
        )
        first_event_ids = {
            segment.first_event_id
            for segment in segments
            if segment.overlaps_window(start=start, end=end, now=current_time)
            and segment.timeline_kind_at(current_time)
            is ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY
        }
        confirmations = (
            await self._label_repository.list_confirmations_with_label_names(
                account_id=account_id,
                first_event_ids=first_event_ids,
            )
        )
        return build_label_timeline(
            segments=segments,
            confirmations=confirmations,
            start=start,
            end=end,
            now=current_time,
        )

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
            return state_response(
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

            result = await self._save_confirmation(
                account_id=account_id,
                target=ConfirmationTarget(
                    segment_id=segment.segment_id,
                    first_event_id=segment.first_event_id,
                    segment_version=version,
                    label_id=label_id,
                ),
                current_time=current_time,
            )
            return confirmed_response(result, now=current_time)

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
            results: list[list[SavedConfirmation]] = []
            for targets in groups:
                confirmed = [
                    await self._save_confirmation(
                        account_id=account_id,
                        target=target,
                        current_time=current_time,
                    )
                    for target in targets
                ]
                results.append(confirmed)
            return batch_response(results, now=current_time)

    async def _validate_batch_groups(
        self,
        *,
        account_id: UUID,
        request: BatchLabelConfirmationRequest,
        current_time: datetime,
    ) -> list[list[ConfirmationTarget]]:
        timelines: dict[date, LabelTimeline] = {}
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
                snapshot = GroupSnapshot(
                    group_version=item.group_version,
                    segments=tuple(
                        SegmentSnapshot(member.segment_id, member.segment_version)
                        for member in item.segments
                    ),
                )
                if not timelines[item.date].contains(snapshot):
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
        return ConfirmationTarget(
            segment_id=segment.segment_id,
            first_event_id=segment.first_event_id,
            segment_version=version,
            label_id=label_id,
        )

    async def _already_confirmed(
        self, account_id: UUID, targets: list[ConfirmationTarget]
    ) -> bool:
        for target in targets:
            confirmation = await self._label_repository.find_confirmation(
                account_id=account_id, first_event_id=target.first_event_id
            )
            if not target.matches(confirmation):
                return False
        return True

    async def _save_confirmation(
        self,
        *,
        account_id: UUID,
        target: ConfirmationTarget,
        current_time: datetime,
    ) -> SavedConfirmation:
        confirmation = await self._label_repository.find_confirmation(
            account_id=account_id, first_event_id=target.first_event_id
        )
        if confirmation is None:
            confirmation = ActivityLabelConfirmation(
                account_id=account_id,
                first_event_id=target.first_event_id,
                segment_version=target.segment_version,
                label_id=target.label_id,
                confirmed_at=current_time,
                updated_at=current_time,
            )
            self._session.add(confirmation)
        else:
            confirmation.apply_selection(
                segment_version=target.segment_version,
                label_id=target.label_id,
                now=current_time,
            )

        await self._session.flush()
        proposal = await self._label_repository.find_proposal(
            account_id=account_id,
            first_event_id=target.first_event_id,
            segment_version=target.segment_version,
        )
        return SavedConfirmation(
            segment_id=target.segment_id,
            segment_version=target.segment_version,
            confirmation=confirmation,
            proposal=proposal,
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
