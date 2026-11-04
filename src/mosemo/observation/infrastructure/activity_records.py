from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.observation.domain.activities import ActivityRecord
from mosemo.observation.infrastructure.tables import activity_records

_MERGE_ORDER = (
    activity_records.c.observed_at,
    activity_records.c.device_id,
    activity_records.c.sequence,
    activity_records.c.event_id,
)


class ActivityRecordRepository:
    async def get_or_create(
        self, db_session: AsyncSession, record: ActivityRecord
    ) -> tuple[ActivityRecord, bool]:
        """Insert the record unless its event ID or device sequence is taken;
        return it and whether this call created it.

        On a conflict the stored record is looked up by event ID first, then by
        device sequence, so the caller can tell which unique key it hit.
        """
        created = await db_session.scalar(
            insert(ActivityRecord)
            .values(
                focus_session_id=record.focus_session_id,
                event_id=record.event_id,
                device_id=record.device_id,
                sequence=record.sequence,
                record_type=record.record_type,
                observed_at=record.observed_at,
                timezone_id=record.timezone_id,
                utc_offset_minutes=record.utc_offset_minutes,
                payload=record.payload,
            )
            # Both unique keys: the event ID primary key and the device sequence.
            .on_conflict_do_nothing()
            .returning(ActivityRecord)
        )
        if created is not None:
            return created, True
        existing = await self.find_by_event_id(db_session, record.event_id)
        if existing is None:
            existing = await self.find_by_device_sequence(
                db_session, device_id=record.device_id, sequence=record.sequence
            )
        if existing is None:
            raise RuntimeError("activity insert conflict could not be resolved")
        return existing, False

    async def find_by_event_id(
        self, db_session: AsyncSession, event_id: UUID
    ) -> ActivityRecord | None:
        return await db_session.get(ActivityRecord, event_id)

    async def find_by_device_sequence(
        self, db_session: AsyncSession, *, device_id: UUID, sequence: int
    ) -> ActivityRecord | None:
        result = await db_session.scalars(
            select(ActivityRecord)
            .where(activity_records.c.device_id == device_id)
            .where(activity_records.c.sequence == sequence)
        )
        return result.one_or_none()

    async def find_last_observed_at(
        self, db_session: AsyncSession, focus_session_id: UUID
    ) -> datetime | None:
        """The latest observation linked to the focus session, if any."""
        return await db_session.scalar(
            select(func.max(activity_records.c.observed_at)).where(
                activity_records.c.focus_session_id == focus_session_id
            )
        )

    async def list_account_records(
        self,
        db_session: AsyncSession,
        device_ids: Sequence[UUID],
        *,
        from_observed_at: datetime,
    ) -> list[ActivityRecord]:
        result = await db_session.scalars(
            select(ActivityRecord)
            .where(
                activity_records.c.device_id.in_(device_ids),
                activity_records.c.observed_at >= from_observed_at,
            )
            .order_by(*_MERGE_ORDER)
        )
        return list(result.all())

    async def find_latest_account_record(
        self, db_session: AsyncSession, device_ids: Sequence[UUID]
    ) -> ActivityRecord | None:
        return await db_session.scalar(
            select(ActivityRecord)
            .where(activity_records.c.device_id.in_(device_ids))
            .order_by(*(column.desc() for column in _MERGE_ORDER))
            .limit(1)
        )

    async def list_record_page(
        self,
        db_session: AsyncSession,
        device_ids: Sequence[UUID],
        *,
        start: datetime,
        after: tuple[datetime, UUID, int, UUID] | None,
        limit: int = 128,
    ) -> list[ActivityRecord]:
        statement = select(ActivityRecord).where(
            activity_records.c.device_id.in_(device_ids),
            activity_records.c.observed_at >= start,
        )
        if after is not None:
            statement = statement.where(tuple_(*_MERGE_ORDER) > after)
        result = await db_session.scalars(
            statement.order_by(*_MERGE_ORDER).limit(limit)
        )
        return list(result.all())

    async def is_account_clock_monotone(
        self, db_session: AsyncSession, device_ids: Sequence[UUID]
    ) -> bool:
        # One-time validation of legacy source before this account's first receipt.
        # Subsequent consumer inserts preserve the invariant through neighbor checks.
        ordered = (
            select(
                activity_records.c.observed_at.label("observed_at"),
                func.lag(activity_records.c.observed_at)
                .over(
                    partition_by=activity_records.c.device_id,
                    order_by=activity_records.c.sequence,
                )
                .label("previous_time"),
            )
            .where(activity_records.c.device_id.in_(device_ids))
            .subquery()
        )
        regression = await db_session.scalar(
            select(ordered.c.observed_at)
            .where(ordered.c.observed_at < ordered.c.previous_time)
            .limit(1)
        )
        return regression is None

    async def find_previous_device_observed_at(
        self, db_session: AsyncSession, record: ActivityRecord
    ) -> datetime | None:
        return await db_session.scalar(
            select(activity_records.c.observed_at)
            .where(activity_records.c.device_id == record.device_id)
            .where(activity_records.c.sequence < record.sequence)
            .order_by(activity_records.c.sequence.desc())
            .limit(1)
        )

    async def find_next_device_observed_at(
        self, db_session: AsyncSession, record: ActivityRecord
    ) -> datetime | None:
        return await db_session.scalar(
            select(activity_records.c.observed_at)
            .where(activity_records.c.device_id == record.device_id)
            .where(activity_records.c.sequence > record.sequence)
            .order_by(activity_records.c.sequence)
            .limit(1)
        )
