from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.enums import ActivityTimelineKind
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.versions import segment_version


@dataclass(frozen=True, slots=True)
class ObservedSegment:
    """An observation segment as seen at the `now` the caller passed in.

    `ended_at`, `kind` and `version` are evaluated at that `now`. `version` is
    `None` unless the segment is an activity whose end is known.
    """

    id: UUID
    account_id: UUID
    first_event_id: UUID
    started_at: datetime
    last_observed_at: datetime | None
    reason: str | None
    context: dict[str, object] | None
    ended_at: datetime | None
    kind: ActivityTimelineKind
    version: str | None


def _observed(segment: ActivityTimelineSegment, *, now: datetime) -> ObservedSegment:
    ended_at = segment.effective_ended_at(now)
    kind = segment.timeline_kind_at(now)
    version = (
        segment_version(segment, ended_at=ended_at)
        if ended_at is not None and kind is not ActivityTimelineKind.CAPTURE_GAP
        else None
    )
    return ObservedSegment(
        id=segment.segment_id,
        account_id=segment.account_id,
        first_event_id=segment.first_event_id,
        started_at=segment.started_at,
        last_observed_at=segment.last_observed_at,
        reason=segment.reason,
        context=segment.context,
        ended_at=ended_at,
        kind=kind,
        version=version,
    )


class ObservationTimeline:
    """Account timeline reads and locking for other contexts.

    Runs inside the caller's transaction and never opens one itself.
    """

    async def lock(self, db_session: AsyncSession, account_id: UUID) -> None:
        """Hold the account timeline lock until the caller's transaction ends.

        Raises `ActivityTimelineBusyError` when the lock is not acquired in time.
        """
        await ActivityRepository(db_session).lock_timeline(account_id)

    async def find_segment(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        segment_id: UUID,
        now: datetime,
    ) -> ObservedSegment | None:
        """The account's segment with this ID, if any."""
        segment = await ActivityRepository(db_session).find_account_segment(
            account_id=account_id, segment_id=segment_id
        )
        return None if segment is None else _observed(segment, now=now)

    async def find_by_first_event(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        first_event_id: UUID,
        now: datetime,
    ) -> ObservedSegment | None:
        """The account's segment that starts with this event, if any."""
        segment = await ActivityRepository(
            db_session
        ).find_account_segment_by_first_event(
            account_id=account_id, first_event_id=first_event_id
        )
        return None if segment is None else _observed(segment, now=now)

    async def find_by_first_events(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        first_event_ids: Collection[UUID],
        now: datetime,
    ) -> dict[UUID, ObservedSegment]:
        """The account's segments keyed by first event; missing ones are left out."""
        segments = await ActivityRepository(
            db_session
        ).list_account_segments_by_first_events(
            account_id=account_id, first_event_ids=first_event_ids
        )
        return {
            segment.first_event_id: _observed(segment, now=now) for segment in segments
        }

    async def list_window(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        start: datetime,
        end: datetime,
        now: datetime,
    ) -> list[ObservedSegment]:
        """The account's segments overlapping `[start, end)` at `now`, in order."""
        segments = await ActivityRepository(db_session).list_date_segments(
            account_id=account_id, start=start, end=end
        )
        return [
            _observed(segment, now=now)
            for segment in segments
            if segment.overlaps_window(start=start, end=end, now=now)
        ]

    async def list_closed_page(
        self,
        db_session: AsyncSession,
        *,
        after_segment_id: UUID | None,
        now: datetime,
        limit: int,
    ) -> list[ObservedSegment]:
        """Closed detailed segments of all accounts after the cursor, by segment ID."""
        segments = await ActivityRepository(db_session).list_closed_detailed_segments(
            after_segment_id=after_segment_id, now=now, limit=limit
        )
        return [_observed(segment, now=now) for segment in segments]
