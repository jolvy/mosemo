from collections.abc import Collection, Sequence
from datetime import datetime
from hashlib import blake2b
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.constants import MAX_OBSERVATION_GAP
from mosemo.activities.enums import SegmentType
from mosemo.activities.models import ActivityRecord as StoredActivityRecord
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.timeline import ProjectedSegment


class ActivityTimelineBusyError(Exception):
    pass


def activity_timeline_lock_key(account_id: UUID) -> int:
    digest = blake2b(
        f"activity_timeline:{account_id}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, signed=True)


class ActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_timeline(self, account_id: UUID) -> None:
        try:
            await self._session.execute(text("SET LOCAL lock_timeout = '3s'"))
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": activity_timeline_lock_key(account_id)},
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise ActivityTimelineBusyError from exc
            raise

    async def add_if_absent(
        self,
        record: StoredActivityRecord,
    ) -> StoredActivityRecord | None:
        statement = (
            insert(StoredActivityRecord)
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

    async def find_last_observed_at(self, session_id: UUID) -> datetime | None:
        return await self._session.scalar(
            select(func.max(StoredActivityRecord.observed_at)).where(
                StoredActivityRecord.focus_session_id == session_id
            )
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
        self, device_ids: Sequence[UUID], *, from_observed_at: datetime
    ) -> list[StoredActivityRecord]:
        result = await self._session.scalars(
            select(StoredActivityRecord)
            .where(
                StoredActivityRecord.device_id.in_(device_ids),
                StoredActivityRecord.observed_at >= from_observed_at,
            )
            .order_by(
                StoredActivityRecord.observed_at,
                StoredActivityRecord.device_id,
                StoredActivityRecord.sequence,
                StoredActivityRecord.event_id,
            )
        )
        return list(result.all())

    async def find_latest_account_record(
        self, device_ids: Sequence[UUID]
    ) -> StoredActivityRecord | None:
        record = StoredActivityRecord
        return await self._session.scalar(
            select(record)
            .where(record.device_id.in_(device_ids))
            .order_by(
                record.observed_at.desc(),
                record.device_id.desc(),
                record.sequence.desc(),
                record.event_id.desc(),
            )
            .limit(1)
        )

    async def list_record_page(
        self,
        device_ids: Sequence[UUID],
        *,
        start: datetime,
        after: tuple[datetime, UUID, int, UUID] | None,
        limit: int = 128,
    ) -> list[StoredActivityRecord]:
        record = StoredActivityRecord
        statement = select(record).where(
            record.device_id.in_(device_ids), record.observed_at >= start
        )
        if after is not None:
            statement = statement.where(
                tuple_(
                    record.observed_at,
                    record.device_id,
                    record.sequence,
                    record.event_id,
                )
                > after
            )
        return list(
            (
                await self._session.scalars(
                    statement.order_by(
                        record.observed_at,
                        record.device_id,
                        record.sequence,
                        record.event_id,
                    ).limit(limit)
                )
            ).all()
        )

    async def is_account_clock_monotone(self, device_ids: Sequence[UUID]) -> bool:
        # One-time validation of legacy source before this account's first receipt.
        # Subsequent consumer inserts preserve the invariant through neighbor checks.
        record = StoredActivityRecord
        ordered = (
            select(
                record.observed_at.label("observed_at"),
                func.lag(record.observed_at)
                .over(partition_by=record.device_id, order_by=record.sequence)
                .label("previous_time"),
            )
            .where(record.device_id.in_(device_ids))
            .subquery()
        )
        regression = await self._session.scalar(
            select(ordered.c.observed_at)
            .where(ordered.c.observed_at < ordered.c.previous_time)
            .limit(1)
        )
        return regression is None

    async def find_previous_device_observed_at(
        self, record: StoredActivityRecord
    ) -> datetime | None:
        model = StoredActivityRecord
        return await self._session.scalar(
            select(model.observed_at)
            .where(model.device_id == record.device_id)
            .where(model.sequence < record.sequence)
            .order_by(model.sequence.desc())
            .limit(1)
        )

    async def find_next_device_observed_at(
        self, record: StoredActivityRecord
    ) -> datetime | None:
        model = StoredActivityRecord
        return await self._session.scalar(
            select(model.observed_at)
            .where(model.device_id == record.device_id)
            .where(model.sequence > record.sequence)
            .order_by(model.sequence)
            .limit(1)
        )

    async def find_last_account_segment(
        self, account_id: UUID
    ) -> ActivityTimelineSegment | None:
        record = StoredActivityRecord
        return await self._session.scalar(
            _ordered_account_segments(account_id)
            .order_by(None)
            .order_by(
                ActivityTimelineSegment.started_at.desc(),
                record.device_id.desc(),
                record.sequence.desc(),
                record.event_id.desc(),
            )
            .limit(1)
        )

    async def list_segment_range(
        self, account_id: UUID, *, start: datetime, through: datetime | None = None
    ) -> list[ActivityTimelineSegment]:
        # Page only the calculated prefix, never pre-read the unaffected suffix.
        segment = ActivityTimelineSegment
        record = StoredActivityRecord
        base = (
            select(segment, record.device_id, record.sequence, record.event_id)
            .join(record, segment.first_event_id == record.event_id)
            .where(segment.account_id == account_id, segment.started_at >= start)
        )
        if through is not None:
            base = base.where(segment.started_at <= through)
        after = None
        segments: list[ActivityTimelineSegment] = []
        ordering = (
            segment.started_at,
            record.device_id,
            record.sequence,
            record.event_id,
        )
        while True:
            statement = base
            if after is not None:
                statement = statement.where(tuple_(*ordering) > after)
            rows = (
                await self._session.execute(statement.order_by(*ordering).limit(128))
            ).all()
            if not rows:
                return segments
            segments.extend(row[0] for row in rows)
            last = rows[-1]
            after = (last[0].started_at, last[1], last[2], last[3])

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

    async def list_account_segments_by_first_events(
        self,
        *,
        account_id: UUID,
        first_event_ids: Collection[UUID],
    ) -> list[ActivityTimelineSegment]:
        if not first_event_ids:
            return []
        result = await self._session.scalars(
            select(ActivityTimelineSegment)
            .where(ActivityTimelineSegment.account_id == account_id)
            .where(ActivityTimelineSegment.first_event_id.in_(first_event_ids))
        )
        return list(result.all())

    async def list_closed_detailed_segments(
        self,
        *,
        after_segment_id: UUID | None,
        now: datetime,
        limit: int,
    ) -> list[ActivityTimelineSegment]:
        statement = (
            select(ActivityTimelineSegment)
            .where(ActivityTimelineSegment.segment_type == SegmentType.ACTIVITY)
            .where(ActivityTimelineSegment.context["kind"].astext == "detailed")
            .where(
                or_(
                    ActivityTimelineSegment.ended_at.is_not(None),
                    ActivityTimelineSegment.last_observed_at
                    < now - MAX_OBSERVATION_GAP,
                )
            )
            .order_by(ActivityTimelineSegment.segment_id)
            .limit(limit)
        )
        if after_segment_id is not None:
            statement = statement.where(
                ActivityTimelineSegment.segment_id > after_segment_id
            )
        return list((await self._session.scalars(statement)).all())

    async def replace_account_segments(
        self,
        *,
        account_id: UUID,
        projected: list[ProjectedSegment],
        existing: list[ActivityTimelineSegment],
        event_observed_at: datetime,
    ) -> None:
        projected, to_delete = self._trim_converged(
            projected, existing=existing, observed_at=event_observed_at
        )
        by_first_event = {segment.first_event_id: segment for segment in to_delete}
        for segment in to_delete:
            await self._session.delete(segment)
        await self._session.flush()

        reused_ids: set[UUID] = set()
        for projected_segment in projected:
            segment_id = self._reusable_id(
                projected_segment,
                by_first_event=by_first_event,
                candidates=to_delete,
                reused_ids=reused_ids,
            )
            if segment_id is not None:
                reused_ids.add(segment_id)
            self._session.add(
                projected_segment.to_segment(
                    account_id=account_id, segment_id=segment_id
                )
            )
        await self._session.flush()

    @staticmethod
    def _trim_converged(
        projected: list[ProjectedSegment],
        *,
        existing: list[ActivityTimelineSegment],
        observed_at: datetime,
    ) -> tuple[list[ProjectedSegment], list[ActivityTimelineSegment]]:
        for new_index, new_segment in enumerate(projected):
            old_index = new_segment.converged_index(existing, observed_at=observed_at)
            if old_index is not None:
                return projected[:new_index], existing[:old_index]
        return projected, existing

    @staticmethod
    def _reusable_id(
        projected: ProjectedSegment,
        *,
        by_first_event: dict[UUID, ActivityTimelineSegment],
        candidates: list[ActivityTimelineSegment],
        reused_ids: set[UUID],
    ) -> UUID | None:
        old = by_first_event.get(projected.first_event_id)
        if old is None or old.segment_id in reused_ids:
            old = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.segment_id not in reused_ids
                    and projected.continues(candidate)
                ),
                None,
            )
        if old is not None and projected.can_inherit(old):
            return old.segment_id
        return None


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
            StoredActivityRecord.device_id,
            StoredActivityRecord.sequence,
            StoredActivityRecord.event_id,
        )
    )
