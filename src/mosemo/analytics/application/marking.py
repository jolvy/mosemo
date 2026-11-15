"""Recompute markers written inside the source change transaction (ADR 0016 「변경 표시」)."""

from collections.abc import Collection
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.domain.markers import MarkScope
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository
from mosemo.identity.public import AccountDirectory
from mosemo.labeling.public import LabelConfirmed
from mosemo.observation.public import (
    ActivityTimelineKind,
    FocusSessionCompleted,
    FocusSessionLog,
    ObservationTimeline,
    TimelineChanged,
)
from mosemo.shared.application.clock import Clock
from mosemo.shared.domain.timezones import Timezone


class DailyStatsMarking:
    """BEFORE_COMMIT handlers that mark the dates a source change affects.

    Each handler takes the account timeline lock first (re-entrant in the same
    transaction) and only then reads the account timezone, so a concurrent
    timezone change cannot leave the marks on dates of the old timezone. The
    marks are written in date order. Today is marked too; its `eligible_at`
    keeps the batch away until the date has ended.
    """

    def __init__(
        self,
        *,
        timeline: ObservationTimeline,
        accounts: AccountDirectory,
        focus_sessions: FocusSessionLog,
        markers: RecomputeMarkerRepository,
        clock: Clock,
    ) -> None:
        self._timeline = timeline
        self._accounts = accounts
        self._focus_sessions = focus_sessions
        self._markers = markers
        self._clock = clock

    async def on_timeline_changed(
        self, event: TimelineChanged, db_session: AsyncSession
    ) -> None:
        """The changed range's dates, the start dates of the completed sessions
        overlapping it, and the end date of the activity segment right before
        it (its silence count depends on the next segment)."""
        account_id = event.account_id
        timezone = await self._lock_and_read_timezone(db_session, account_id)
        start, end = event.changed_from, event.changed_to
        dates = MarkScope.dates_for_range(timezone, start=start, end=end)
        dates |= await self._session_start_dates(
            db_session, account_id, timezone=timezone, start=start, end=end
        )
        preceding = await self._preceding_activity_end(
            db_session, account_id, before=start
        )
        if preceding is not None:
            dates.add(MarkScope.date_of(timezone, preceding))
        await self._mark(db_session, account_id, timezone=timezone, dates=dates)

    async def on_label_confirmed(
        self, event: LabelConfirmed, db_session: AsyncSession
    ) -> None:
        """Every date the confirmed segment overlaps (its pending time) and the
        start dates of the completed sessions overlapping it."""
        account_id = event.account_id
        timezone = await self._lock_and_read_timezone(db_session, account_id)
        start, end = event.segment_started_at, event.segment_ended_at
        dates = MarkScope.dates_for_range(timezone, start=start, end=end)
        dates |= await self._session_start_dates(
            db_session, account_id, timezone=timezone, start=start, end=end
        )
        await self._mark(db_session, account_id, timezone=timezone, dates=dates)

    async def on_focus_session_completed(
        self, event: FocusSessionCompleted, db_session: AsyncSession
    ) -> None:
        """Only the session start date: session totals belong to that date."""
        account_id = event.account_id
        timezone = await self._lock_and_read_timezone(db_session, account_id)
        dates = {MarkScope.date_of(timezone, event.started_at)}
        await self._mark(db_session, account_id, timezone=timezone, dates=dates)

    async def _lock_and_read_timezone(
        self, db_session: AsyncSession, account_id: UUID
    ) -> Timezone:
        await self._timeline.lock(db_session, account_id)
        return await self._accounts.get_timezone(db_session, account_id)

    async def _session_start_dates(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        timezone: Timezone,
        start: datetime,
        end: datetime,
    ) -> set[date]:
        sessions = await self._focus_sessions.list_completed_overlapping(
            db_session, account_id, start=start, end=end
        )
        return {MarkScope.date_of(timezone, session.started_at) for session in sessions}

    async def _preceding_activity_end(
        self, db_session: AsyncSession, account_id: UUID, *, before: datetime
    ) -> datetime | None:
        """The effective end of the segment right before `before`, unless it is
        a capture gap or there is none."""
        preceding = await self._timeline.list_preceding(
            db_session, account_id, before=before, limit=1, now=self._clock()
        )
        if not preceding or preceding[0].kind is ActivityTimelineKind.CAPTURE_GAP:
            return None
        return preceding[0].ended_at or preceding[0].last_observed_at

    async def _mark(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        timezone: Timezone,
        dates: Collection[date],
    ) -> None:
        now = self._clock()
        for local_date in sorted(dates):
            await self._markers.upsert_source(
                db_session,
                account_id=account_id,
                local_date=local_date,
                eligible_at=MarkScope.eligible_at(local_date, timezone),
                now=now,
            )
