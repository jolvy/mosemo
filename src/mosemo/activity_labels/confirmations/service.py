from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.enums import ActivityTimelineKind
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.versions import segment_version
from mosemo.activity_labels.catalog.repository import LabelCatalogRepository
from mosemo.activity_labels.confirmations.models import ActivityLabelConfirmation
from mosemo.activity_labels.confirmations.repository import ConfirmationRepository
from mosemo.activity_labels.schemas import (
    ActivityLabelConfirmationRequest,
    BatchLabelConfirmationRequest,
    SegmentLabelConfirmationItemRequest,
)


class ActivityLabelSegmentNotFoundError(Exception):
    pass


class ActivityLabelSegmentNotLabelableError(Exception):
    pass


class ActivityLabelSegmentChangedError(Exception):
    pass


class ActivityLabelUnavailableError(Exception):
    pass


class ActivityLabelConfirmationConflictError(Exception):
    pass


class ActivityLabelSegmentDuplicateError(Exception):
    pass


class BatchLabelConfirmationFailure(Exception):
    def __init__(
        self,
        *,
        item_index: int,
        reason: Exception,
        field_name: str | None = None,
    ) -> None:
        super().__init__(str(reason))
        self.item_index = item_index
        self.field_name = field_name
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ConfirmationTarget:
    segment: ActivityTimelineSegment
    version: str
    label_id: UUID | None


@dataclass(frozen=True, slots=True)
class SavedConfirmation:
    segment: ActivityTimelineSegment
    version: str
    confirmation: ActivityLabelConfirmation


class ConfirmationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        activity_repository: ActivityRepository,
        confirmation_repository: ConfirmationRepository,
        label_catalog_repository: LabelCatalogRepository,
    ) -> None:
        self._session = session
        self._activity_repository = activity_repository
        self._confirmation_repository = confirmation_repository
        self._label_catalog_repository = label_catalog_repository

    async def confirm(
        self,
        *,
        account_id: UUID,
        segment_id: UUID,
        request: ActivityLabelConfirmationRequest,
        current_time: datetime,
    ) -> SavedConfirmation:
        segment = await find_labelable_segment(
            self._activity_repository,
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
            label = await self._label_catalog_repository.find_active_owned(
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
        current_time: datetime,
    ) -> list[SavedConfirmation]:
        self.reject_duplicate_segments(request)
        targets = await self._validate_batch_items(
            account_id=account_id, request=request, current_time=current_time
        )
        return [
            await self._save_confirmation(
                account_id=account_id, target=target, current_time=current_time
            )
            for target in targets
        ]

    @staticmethod
    def reject_duplicate_segments(request: BatchLabelConfirmationRequest) -> None:
        seen: set[UUID] = set()
        for item_index, item in enumerate(request.items):
            if item.segment_id in seen:
                raise ConfirmationService._batch_failure(
                    item_index=item_index,
                    reason=ActivityLabelSegmentDuplicateError(),
                    field_name="segmentId",
                )
            seen.add(item.segment_id)

    @staticmethod
    def _batch_failure(
        *, item_index: int, reason: Exception, field_name: str
    ) -> BatchLabelConfirmationFailure:
        return BatchLabelConfirmationFailure(
            item_index=item_index, reason=reason, field_name=field_name
        )

    async def _validate_batch_items(
        self,
        *,
        account_id: UUID,
        request: BatchLabelConfirmationRequest,
        current_time: datetime,
    ) -> list[ConfirmationTarget]:
        targets = []
        for item_index, item in enumerate(request.items):
            target = await self._validate_batch_item(
                account_id=account_id,
                item=item,
                item_index=item_index,
                current_time=current_time,
            )
            targets.append(target)
        return targets

    async def _validate_batch_item(
        self,
        *,
        account_id: UUID,
        item: SegmentLabelConfirmationItemRequest,
        item_index: int,
        current_time: datetime,
    ) -> ConfirmationTarget:
        segment = await self._find_batch_segment(
            account_id=account_id,
            item=item,
            item_index=item_index,
            current_time=current_time,
        )
        version = self._validate_batch_segment_version(
            item=item,
            item_index=item_index,
            segment=segment,
            current_time=current_time,
        )
        label_id = getattr(item.selection, "label_id", None)
        confirmation = await self._find_batch_confirmation(
            account_id=account_id, segment=segment
        )
        is_idempotent = self._validate_batch_selection(
            item_index=item_index,
            label_id=label_id,
            segment_version=version,
            confirmation=confirmation,
        )
        await self._validate_batch_label_availability(
            account_id=account_id,
            item_index=item_index,
            label_id=label_id,
            is_idempotent=is_idempotent,
        )
        return ConfirmationTarget(segment=segment, version=version, label_id=label_id)

    async def _find_batch_segment(
        self,
        *,
        account_id: UUID,
        item: SegmentLabelConfirmationItemRequest,
        item_index: int,
        current_time: datetime,
    ) -> ActivityTimelineSegment:
        try:
            return await find_labelable_segment(
                self._activity_repository,
                account_id=account_id,
                segment_id=item.segment_id,
                current_time=current_time,
            )
        except (
            ActivityLabelSegmentNotFoundError,
            ActivityLabelSegmentNotLabelableError,
        ) as exc:
            raise self._batch_failure(
                item_index=item_index, reason=exc, field_name="segmentId"
            ) from exc

    @staticmethod
    def _validate_batch_segment_version(
        *,
        item: SegmentLabelConfirmationItemRequest,
        item_index: int,
        segment: ActivityTimelineSegment,
        current_time: datetime,
    ) -> str:
        ended_at = segment.effective_ended_at(current_time)
        assert ended_at is not None
        version = segment_version(segment, ended_at=ended_at)
        if item.segment_version != version:
            raise ConfirmationService._batch_failure(
                item_index=item_index,
                reason=ActivityLabelSegmentChangedError(),
                field_name="segmentVersion",
            )
        return version

    async def _find_batch_confirmation(
        self, *, account_id: UUID, segment: ActivityTimelineSegment
    ) -> ActivityLabelConfirmation | None:
        return await self._confirmation_repository.find_confirmation(
            account_id=account_id,
            first_event_id=segment.first_event_id,
        )

    @staticmethod
    def _validate_batch_selection(
        *,
        item_index: int,
        label_id: UUID | None,
        segment_version: str,
        confirmation: ActivityLabelConfirmation | None,
    ) -> bool:
        if confirmation is None:
            return False
        accepts_selection = confirmation.accepts_batch_selection(
            segment_version=segment_version, label_id=label_id
        )
        if not accepts_selection:
            raise ConfirmationService._batch_failure(
                item_index=item_index,
                reason=ActivityLabelConfirmationConflictError(),
                field_name="selection",
            )
        return confirmation.matches_batch_selection(
            segment_version=segment_version, label_id=label_id
        )

    async def _validate_batch_label_availability(
        self,
        *,
        account_id: UUID,
        item_index: int,
        label_id: UUID | None,
        is_idempotent: bool,
    ) -> None:
        if label_id is None or is_idempotent:
            return
        label = await self._label_catalog_repository.find_active_owned(
            account_id=account_id,
            label_id=label_id,
        )
        if label is None:
            raise self._batch_failure(
                item_index=item_index,
                reason=ActivityLabelUnavailableError(),
                field_name="selection",
            )

    async def _save_confirmation(
        self,
        *,
        account_id: UUID,
        target: ConfirmationTarget,
        current_time: datetime,
    ) -> SavedConfirmation:
        segment = target.segment
        confirmation = await self._confirmation_repository.find_confirmation(
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
        return SavedConfirmation(
            segment=segment, version=target.version, confirmation=confirmation
        )


async def find_labelable_segment(
    activity_repository: ActivityRepository,
    *,
    account_id: UUID,
    segment_id: UUID,
    current_time: datetime,
) -> ActivityTimelineSegment:
    segment = await activity_repository.find_account_segment(
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
