from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account
from mosemo.activities.enums import SegmentType
from mosemo.activities.models import ActivityRecord as StoredActivityRecord
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.schemas import ActivityObservation
from mosemo.activities.schemas import ActivityRecord as ActivityRecordRequest
from mosemo.activities.timeline import (
    ProjectedActivity,
    ProjectedSegment,
)
from mosemo.devices.models import Device
from mosemo.timezones import Timezone


def activity_payload(record: ActivityRecordRequest) -> dict[str, object]:
    if isinstance(record, ActivityObservation):
        return {"context": record.context.model_dump(mode="json", by_alias=True)}
    return {"state": record.state.value, "reason": record.reason}


def is_same_activity_record(
    stored: StoredActivityRecord,
    requested: ActivityRecordRequest,
) -> bool:
    return (
        stored.event_id == requested.event_id
        and stored.device_id == requested.device_id
        and stored.sequence == requested.sequence
        and stored.record_type == requested.record_type
        and stored.observed_at == requested.observed_at
        and stored.timezone_id == requested.timezone_id
        and stored.utc_offset_minutes == requested.utc_offset_minutes
        and stored.payload == activity_payload(requested)
    )


class ActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(
        self,
        record: ActivityRecordRequest,
    ) -> StoredActivityRecord | None:
        statement = (
            insert(StoredActivityRecord)
            .values(
                event_id=record.event_id,
                device_id=record.device_id,
                sequence=record.sequence,
                record_type=record.record_type,
                observed_at=record.observed_at,
                timezone_id=record.timezone_id,
                utc_offset_minutes=record.utc_offset_minutes,
                payload=activity_payload(record),
            )
            .on_conflict_do_nothing()
            .returning(StoredActivityRecord)
        )
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def find_by_event_id(
        self,
        event_id: UUID,
    ) -> StoredActivityRecord | None:
        return await self._session.get(StoredActivityRecord, event_id)

    async def find_by_device_sequence(
        self,
        *,
        device_id: UUID,
        sequence: int,
    ) -> StoredActivityRecord | None:
        result = await self._session.scalars(
            select(StoredActivityRecord)
            .where(StoredActivityRecord.device_id == device_id)
            .where(StoredActivityRecord.sequence == sequence)
        )
        return result.one_or_none()

    async def find_account_timezone(self, account_id: UUID) -> Timezone | None:
        return await self._session.scalar(
            select(Account.timezone).where(Account.account_id == account_id)
        )

    async def find_replay_start(
        self, account_id: UUID, observed_at: datetime
    ) -> datetime:
        previous_start = await self._session.scalar(
            select(func.max(ActivityTimelineSegment.started_at)).where(
                ActivityTimelineSegment.account_id == account_id,
                ActivityTimelineSegment.started_at < observed_at,
            )
        )
        return previous_start or observed_at

    async def list_account_records(
        self, account_id: UUID, *, from_observed_at: datetime
    ) -> list[StoredActivityRecord]:
        result = await self._session.scalars(
            select(StoredActivityRecord)
            .join(Device, StoredActivityRecord.device_id == Device.device_id)
            .where(
                Device.account_id == account_id,
                StoredActivityRecord.observed_at >= from_observed_at,
            )
            .order_by(
                StoredActivityRecord.observed_at,
                StoredActivityRecord.received_at,
                StoredActivityRecord.event_id,
            )
        )
        return list(result.all())

    async def list_account_segments(
        self, account_id: UUID, *, from_started_at: datetime | None = None
    ) -> list[ActivityTimelineSegment]:
        statement = _ordered_account_segments(account_id)
        if from_started_at is not None:
            statement = statement.where(
                ActivityTimelineSegment.started_at >= from_started_at
            )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def list_date_segments(
        self,
        *,
        account_id: UUID,
        start: datetime,
        end: datetime,
    ) -> list[ActivityTimelineSegment]:
        segment = ActivityTimelineSegment
        statement = _ordered_account_segments(account_id).where(
            or_(
                and_(segment.started_at < end, segment.ended_at > start),
                and_(
                    segment.ended_at == segment.started_at,
                    segment.started_at >= start,
                    segment.started_at < end,
                ),
                and_(
                    segment.segment_type == SegmentType.ACTIVITY,
                    segment.ended_at.is_(None),
                    segment.started_at < end,
                    segment.last_observed_at >= start,
                ),
                and_(
                    segment.segment_type == SegmentType.CAPTURE_GAP,
                    segment.ended_at.is_(None),
                    segment.started_at < end,
                ),
            )
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def find_account_segment(
        self,
        *,
        account_id: UUID,
        segment_id: UUID,
    ) -> ActivityTimelineSegment | None:
        return await self._session.scalar(
            select(ActivityTimelineSegment)
            .where(ActivityTimelineSegment.account_id == account_id)
            .where(ActivityTimelineSegment.segment_id == segment_id)
        )

    async def find_account_segment_by_first_event(
        self,
        *,
        account_id: UUID,
        first_event_id: UUID,
    ) -> ActivityTimelineSegment | None:
        return await self._session.scalar(
            select(ActivityTimelineSegment)
            .where(ActivityTimelineSegment.account_id == account_id)
            .where(ActivityTimelineSegment.first_event_id == first_event_id)
        )

    async def replace_account_segments(
        self,
        *,
        account_id: UUID,
        projected: list[ProjectedSegment],
        existing: list[ActivityTimelineSegment],
        event_observed_at: datetime,
    ) -> None:
        convergence: tuple[int, int] | None = None
        for new_index, new_segment in enumerate(projected):
            old_index = converged_old_index(existing, new_segment, event_observed_at)
            if old_index is not None:
                convergence = (new_index, old_index)
                break

        if convergence is not None:
            projected = projected[: convergence[0]]
            to_delete = existing[: convergence[1]]
        else:
            to_delete = existing
        by_first_event = {segment.first_event_id: segment for segment in to_delete}
        for segment in to_delete:
            await self._session.delete(segment)
        await self._session.flush()

        reused_ids: set[UUID] = set()
        for projected_segment in projected:
            old = by_first_event.get(projected_segment.first_event_id)
            segment_type = (
                SegmentType.ACTIVITY
                if isinstance(projected_segment, ProjectedActivity)
                else SegmentType.CAPTURE_GAP
            )
            if old is None or old.segment_id in reused_ids:
                old = _merged_existing_segment(
                    to_delete, projected_segment, reused_ids=reused_ids
                )
            segment_id = (
                old.segment_id
                if old is not None and old.segment_type == segment_type
                else uuid7()
            )
            reused_ids.add(segment_id)
            segment = ActivityTimelineSegment(
                segment_id=segment_id,
                account_id=account_id,
                segment_type=segment_type,
                started_at=projected_segment.started_at,
                ended_at=projected_segment.ended_at,
                first_event_id=projected_segment.first_event_id,
                last_event_id=(
                    projected_segment.last_event_id
                    if isinstance(projected_segment, ProjectedActivity)
                    else None
                ),
                last_observed_at=(
                    projected_segment.last_observed_at
                    if isinstance(projected_segment, ProjectedActivity)
                    else None
                ),
                context=(
                    projected_segment.context
                    if isinstance(projected_segment, ProjectedActivity)
                    else None
                ),
                reason=(
                    projected_segment.reason
                    if not isinstance(projected_segment, ProjectedActivity)
                    else None
                ),
            )
            self._session.add(segment)
        await self._session.flush()


def _reaches_event(segment: ProjectedSegment, observed_at: datetime) -> bool:
    if segment.started_at > observed_at:
        return True
    if segment.ended_at is None:
        return segment.started_at <= observed_at
    return segment.ended_at > observed_at


def _ordered_account_segments(account_id: UUID):
    return (
        select(ActivityTimelineSegment)
        .join(
            StoredActivityRecord,
            ActivityTimelineSegment.first_event_id == StoredActivityRecord.event_id,
        )
        .where(ActivityTimelineSegment.account_id == account_id)
        .order_by(
            ActivityTimelineSegment.started_at,
            StoredActivityRecord.received_at,
            StoredActivityRecord.event_id,
        )
    )


def converged_old_index(
    existing: list[ActivityTimelineSegment],
    projected: ProjectedSegment,
    observed_at: datetime,
) -> int | None:
    if not _reaches_event(projected, observed_at):
        return None
    for index, old in enumerate(existing):
        if _same_projected_segment(old, projected):
            return index
    return None


def _same_projected_segment(
    stored: ActivityTimelineSegment, projected: ProjectedSegment
) -> bool:
    if isinstance(projected, ProjectedActivity):
        return (
            stored.segment_type is SegmentType.ACTIVITY
            and stored.started_at == projected.started_at
            and stored.ended_at == projected.ended_at
            and stored.first_event_id == projected.first_event_id
            and stored.last_event_id == projected.last_event_id
            and stored.last_observed_at == projected.last_observed_at
            and stored.context == projected.context
        )
    return (
        stored.segment_type is SegmentType.CAPTURE_GAP
        and stored.started_at == projected.started_at
        and stored.ended_at == projected.ended_at
        and stored.first_event_id == projected.first_event_id
        and stored.reason == projected.reason
    )


def _merged_existing_segment(
    existing: list[ActivityTimelineSegment],
    projected: ProjectedSegment,
    *,
    reused_ids: set[UUID],
) -> ActivityTimelineSegment | None:
    segment_type = (
        SegmentType.ACTIVITY
        if isinstance(projected, ProjectedActivity)
        else SegmentType.CAPTURE_GAP
    )
    for old in existing:
        if old.segment_id in reused_ids:
            continue
        if old.segment_type is not segment_type:
            continue
        if (
            isinstance(projected, ProjectedActivity)
            and old.context != projected.context
        ):
            continue
        if old.started_at >= projected.started_at and (
            projected.ended_at is None or old.started_at <= projected.ended_at
        ):
            return old
    return None
