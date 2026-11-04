from collections.abc import Collection
from datetime import datetime
from hashlib import blake2b
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select, text, tuple_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.observation.domain.activities import MAX_OBSERVATION_GAP
from mosemo.observation.domain.timeline import (
    ActivityTimelineBusyError,
    ActivityTimelineSegment,
    ProjectedSegment,
    SegmentType,
)
from mosemo.observation.infrastructure.tables import (
    activity_records,
    activity_timeline_segments,
)

segments = activity_timeline_segments
records = activity_records


def activity_timeline_lock_key(account_id: UUID) -> int:
    digest = blake2b(
        f"activity_timeline:{account_id}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, signed=True)


class TimelineSegmentRepository:
    async def lock_timeline(self, db_session: AsyncSession, account_id: UUID) -> None:
        """Hold the account timeline advisory lock until the transaction ends."""
        try:
            await db_session.execute(text("SET LOCAL lock_timeout = '3s'"))
            await db_session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": activity_timeline_lock_key(account_id)},
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise ActivityTimelineBusyError from exc
            raise

    async def find_replay_start(
        self, db_session: AsyncSession, account_id: UUID, *, observed_at: datetime
    ) -> datetime:
        previous_start: datetime | None = await db_session.scalar(
            select(func.max(segments.c.started_at)).where(
                segments.c.account_id == account_id,
                segments.c.started_at < observed_at,
            )
        )
        return previous_start or observed_at

    async def find_last_account_segment(
        self, db_session: AsyncSession, account_id: UUID
    ) -> ActivityTimelineSegment | None:
        return await db_session.scalar(
            _ordered_account_segments(account_id)
            .order_by(None)
            .order_by(
                segments.c.started_at.desc(),
                records.c.device_id.desc(),
                records.c.sequence.desc(),
                records.c.event_id.desc(),
            )
            .limit(1)
        )

    async def list_segment_range(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        start: datetime,
        through: datetime | None = None,
    ) -> list[ActivityTimelineSegment]:
        # Page only the calculated prefix, never pre-read the unaffected suffix.
        base = (
            select(
                ActivityTimelineSegment,
                records.c.device_id,
                records.c.sequence,
                records.c.event_id,
            )
            .join(records, segments.c.first_event_id == records.c.event_id)
            .where(segments.c.account_id == account_id, segments.c.started_at >= start)
        )
        if through is not None:
            base = base.where(segments.c.started_at <= through)
        after = None
        found: list[ActivityTimelineSegment] = []
        ordering = (
            segments.c.started_at,
            records.c.device_id,
            records.c.sequence,
            records.c.event_id,
        )
        while True:
            statement = base
            if after is not None:
                statement = statement.where(tuple_(*ordering) > after)
            rows = (
                await db_session.execute(statement.order_by(*ordering).limit(128))
            ).all()
            if not rows:
                return found
            found.extend(row[0] for row in rows)
            last = rows[-1]
            after = (last[0].started_at, last[1], last[2], last[3])

    async def list_account_segments(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        from_started_at: datetime | None = None,
    ) -> list[ActivityTimelineSegment]:
        statement = _ordered_account_segments(account_id)
        if from_started_at is not None:
            statement = statement.where(segments.c.started_at >= from_started_at)
        result = await db_session.scalars(statement)
        return list(result.all())

    async def list_date_segments(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        start: datetime,
        end: datetime,
    ) -> list[ActivityTimelineSegment]:
        statement = _ordered_account_segments(account_id).where(
            or_(
                and_(segments.c.started_at < end, segments.c.ended_at > start),
                and_(
                    segments.c.ended_at == segments.c.started_at,
                    segments.c.started_at >= start,
                    segments.c.started_at < end,
                ),
                and_(
                    segments.c.segment_type == SegmentType.ACTIVITY,
                    segments.c.ended_at.is_(None),
                    segments.c.started_at < end,
                    segments.c.last_observed_at >= start,
                ),
                and_(
                    segments.c.segment_type == SegmentType.CAPTURE_GAP,
                    segments.c.ended_at.is_(None),
                    segments.c.started_at < end,
                ),
            )
        )
        result = await db_session.scalars(statement)
        return list(result.all())

    async def find_account_segment(
        self, db_session: AsyncSession, *, account_id: UUID, segment_id: UUID
    ) -> ActivityTimelineSegment | None:
        return await db_session.scalar(
            select(ActivityTimelineSegment)
            .where(segments.c.account_id == account_id)
            .where(segments.c.id == segment_id)
        )

    async def find_account_segment_by_first_event(
        self, db_session: AsyncSession, *, account_id: UUID, first_event_id: UUID
    ) -> ActivityTimelineSegment | None:
        return await db_session.scalar(
            select(ActivityTimelineSegment)
            .where(segments.c.account_id == account_id)
            .where(segments.c.first_event_id == first_event_id)
        )

    async def list_account_segments_by_first_events(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        first_event_ids: Collection[UUID],
    ) -> list[ActivityTimelineSegment]:
        if not first_event_ids:
            return []
        result = await db_session.scalars(
            select(ActivityTimelineSegment)
            .where(segments.c.account_id == account_id)
            .where(segments.c.first_event_id.in_(first_event_ids))
        )
        return list(result.all())

    async def list_closed_detailed_segments(
        self,
        db_session: AsyncSession,
        *,
        after_segment_id: UUID | None,
        now: datetime,
        limit: int,
    ) -> list[ActivityTimelineSegment]:
        statement = (
            select(ActivityTimelineSegment)
            .where(segments.c.segment_type == SegmentType.ACTIVITY)
            .where(segments.c.context["kind"].astext == "detailed")
            .where(
                or_(
                    segments.c.ended_at.is_not(None),
                    segments.c.last_observed_at < now - MAX_OBSERVATION_GAP,
                )
            )
            .order_by(segments.c.id)
            .limit(limit)
        )
        if after_segment_id is not None:
            statement = statement.where(segments.c.id > after_segment_id)
        return list((await db_session.scalars(statement)).all())

    async def replace_account_segments(
        self,
        db_session: AsyncSession,
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
            await db_session.delete(segment)
        await db_session.flush()

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
            db_session.add(
                projected_segment.to_segment(
                    account_id=account_id, segment_id=segment_id
                )
            )
        await db_session.flush()

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
        if old is None or old.id in reused_ids:
            old = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.id not in reused_ids and projected.continues(candidate)
                ),
                None,
            )
        if old is not None and projected.can_inherit(old):
            return old.id
        return None


def _ordered_account_segments(
    account_id: UUID,
) -> Select[tuple[ActivityTimelineSegment]]:
    return (
        select(ActivityTimelineSegment)
        .join(records, segments.c.first_event_id == records.c.event_id)
        .where(segments.c.account_id == account_id)
        .order_by(
            segments.c.started_at,
            records.c.device_id,
            records.c.sequence,
            records.c.event_id,
        )
    )
