import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.enums import SegmentType
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.service import (
    acquire_activity_timeline_lock,
    timeline_ended_at,
)
from mosemo.labels.models import ActivityLabelConfirmation
from mosemo.labels.repository import LabelRepository
from mosemo.labels.schemas import (
    ActivityLabelConfirmationRequest,
    ActivityLabelStateResponse,
    ConfirmedActivityLabelStateResponse,
    LabelSelectionResponse,
    PendingActivityLabelStateResponse,
    UnclassifiedSelectionResponse,
)


class ActivityLabelSegmentNotFoundError(Exception):
    pass


class ActivityLabelSegmentNotLabelableError(Exception):
    pass


class ActivityLabelSegmentChangedError(Exception):
    pass


class ActivityLabelUnavailableError(Exception):
    pass


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
    ) -> None:
        self._session = session
        self._activity_repository = activity_repository
        self._label_repository = label_repository

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
            ended_at = timeline_ended_at(segment, current_time=current_time)
            assert ended_at is not None
            version = segment_version(segment, ended_at=ended_at)
            confirmation = await self._label_repository.find_confirmation(
                account_id=account_id,
                first_event_id=segment.first_event_id,
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
            ended_at = timeline_ended_at(segment, current_time=current_time)
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
            return self._confirmed_response(
                segment_id=segment.segment_id,
                version=version,
                confirmation=confirmation,
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
        effective_ended_at = timeline_ended_at(segment, current_time=current_time)
        if (
            segment.segment_type is not SegmentType.ACTIVITY
            or segment.context is None
            or segment.context.get("kind") != "detailed"
            or effective_ended_at is None
        ):
            raise ActivityLabelSegmentNotLabelableError
        return segment

    @staticmethod
    def _state_response(
        *,
        segment_id: UUID,
        version: str,
        confirmation: ActivityLabelConfirmation | None,
    ) -> ActivityLabelStateResponse:
        if confirmation is None:
            return PendingActivityLabelStateResponse(
                segment_id=segment_id,
                segment_version=version,
                state="pending",
            )
        return ActivityLabelService._confirmed_response(
            segment_id=segment_id,
            version=version,
            confirmation=confirmation,
        )

    @staticmethod
    def _confirmed_response(
        *,
        segment_id: UUID,
        version: str,
        confirmation: ActivityLabelConfirmation,
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
        )
