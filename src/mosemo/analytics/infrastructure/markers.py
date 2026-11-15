from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Select, delete, false, literal, literal_column, select, update
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.domain.markers import MarkerKind, RecomputeMarker, RetryPolicy
from mosemo.analytics.infrastructure.tables import (
    analytics_daily_markers,
    marker_version_seq,
)

markers = analytics_daily_markers
# Written as the index expression of `analytics_daily_markers_claim_idx` so the
# planner can match it; a bound parameter in its place would not match.
IS_DEFINITION = (markers.c.kind == literal_column("'definition'")).self_group()


def due_markers(
    *, definition: bool, now: datetime, limit: int, policy: RetryPolicy
) -> Select[tuple[RecomputeMarker]]:
    """The claim query of one marker kind, oldest version first."""
    kind = IS_DEFINITION if definition else IS_DEFINITION == false()
    return (
        select(RecomputeMarker)
        .where(
            kind,
            markers.c.eligible_at <= now,
            markers.c.next_attempt_at <= now,
            # Inlined so the partial index predicate `attempts < 6` is provable.
            markers.c.attempts
            < literal(policy.quarantine_threshold, literal_execute=True),
        )
        .order_by(IS_DEFINITION, markers.c.version)
        .limit(limit)
        .with_for_update(skip_locked=True)
        # Claim counts from the locked row, not from an object loaded earlier.
        .execution_options(populate_existing=True)
    )


def marker_upsert(
    kind: MarkerKind,
    *,
    account_id: UUID,
    local_date: date,
    eligible_at: datetime,
    now: datetime,
) -> Insert:
    """Insert, or give the existing marker a new version and `kind` and reset
    its retry state. The version of an existing marker is drawn in the DO
    UPDATE clause, that is after the row lock."""
    statement = insert(markers).values(
        account_id=account_id,
        local_date=local_date,
        version=marker_version_seq.next_value(),
        kind=kind,
        marked_at=now,
        eligible_at=eligible_at,
        attempts=0,
        next_attempt_at=now,
        last_error=None,
        last_failed_at=None,
    )
    return statement.on_conflict_do_update(
        index_elements=[markers.c.account_id, markers.c.local_date],
        set_={
            "version": marker_version_seq.next_value(),
            "kind": statement.excluded.kind,
            "marked_at": statement.excluded.marked_at,
            "eligible_at": statement.excluded.eligible_at,
            "attempts": 0,
            "next_attempt_at": statement.excluded.next_attempt_at,
            "last_error": None,
            "last_failed_at": None,
        },
    )


class RecomputeMarkerRepository:
    async def find(
        self, db_session: AsyncSession, *, account_id: UUID, local_date: date
    ) -> RecomputeMarker | None:
        return await db_session.scalar(
            select(RecomputeMarker)
            .where(
                markers.c.account_id == account_id,
                markers.c.local_date == local_date,
            )
            .execution_options(populate_existing=True)
        )

    async def upsert_source(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        local_date: date,
        eligible_at: datetime,
        now: datetime,
    ) -> None:
        """Write a new source version and reset the retry state.

        The version of an existing marker is drawn in the DO UPDATE clause, that
        is after the row lock, so a later writer always gets a larger version.
        """
        await db_session.execute(
            marker_upsert(
                MarkerKind.SOURCE,
                account_id=account_id,
                local_date=local_date,
                eligible_at=eligible_at,
                now=now,
            )
        )

    async def upsert_definition(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        local_date: date,
        eligible_at: datetime,
        now: datetime,
    ) -> None:
        """Like `upsert_source`, for a date stored with an older definition; an
        existing marker gets a new version and becomes a definition marker."""
        await db_session.execute(
            marker_upsert(
                MarkerKind.DEFINITION,
                account_id=account_id,
                local_date=local_date,
                eligible_at=eligible_at,
                now=now,
            )
        )

    async def claim_due(
        self,
        db_session: AsyncSession,
        *,
        now: datetime,
        limit: int,
        definition_limit: int,
        policy: RetryPolicy,
    ) -> list[RecomputeMarker]:
        """Claim up to `limit` due markers, source markers first, and at most
        `definition_limit` definition markers among them.

        Run in a short transaction of its own: the claim is the counted attempt
        and the lease; the returned versions are the ones read at the claim.
        """
        claimed = list(
            await db_session.scalars(
                due_markers(definition=False, now=now, limit=limit, policy=policy)
            )
        )
        room = min(limit - len(claimed), definition_limit)
        if room > 0:
            claimed += await db_session.scalars(
                due_markers(definition=True, now=now, limit=room, policy=policy)
            )
        for marker in claimed:
            marker.claim(now=now, policy=policy)
        await db_session.flush()
        return claimed

    async def delete_if_version(self, db_session: AsyncSession, version: int) -> bool:
        """Delete the marker only if no change has written a new version since."""
        deleted = await db_session.scalar(
            delete(markers)
            .where(markers.c.version == version)
            .returning(markers.c.version)
        )
        return deleted is not None

    async def release_claim(
        self, db_session: AsyncSession, released: RecomputeMarker
    ) -> bool:
        """Store the attempts and due time of `released` (after its `release`)
        only if the marker still has the version that was claimed."""
        updated = await db_session.scalar(
            update(markers)
            .where(markers.c.version == released.version)
            .values(
                attempts=released.attempts,
                next_attempt_at=released.next_attempt_at,
            )
            .returning(markers.c.version)
        )
        return updated is not None

    async def record_failure(
        self, db_session: AsyncSession, failed: RecomputeMarker
    ) -> bool:
        """Store the backoff and failure class of `failed` (after its `fail`)
        only if the marker still has the version that was claimed."""
        updated = await db_session.scalar(
            update(markers)
            .where(markers.c.version == failed.version)
            .values(
                next_attempt_at=failed.next_attempt_at,
                last_error=failed.last_error,
                last_failed_at=failed.last_failed_at,
            )
            .returning(markers.c.version)
        )
        return updated is not None
