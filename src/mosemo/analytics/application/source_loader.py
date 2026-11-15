"""Reads the source of one date or one session into a `SourceSnapshot`.

ADR 0016 「일간 통계 계산」 reads: D1 rows of each window, the D2 backward read
from its first closed row, and the segment after the last closed activity
ending on the date (P7-B15). The reads give the same segments as
`select_day_segments` over the whole timeline.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.domain.day_reading import BackwardRead, ends_inside
from mosemo.analytics.domain.source import (
    MICROSECOND,
    CompletedFocusSession,
    ConfirmedLabel,
    DayWindow,
    Interval,
    SegmentKind,
    SourceSegment,
    SourceSnapshot,
)
from mosemo.analytics.domain.usage_targets import UsageTargetResolver
from mosemo.labeling.public import LabelConfirmationDirectory, LabelConfirmationView
from mosemo.observation.public import (
    ActivityTimelineKind,
    CompletedFocusSessionView,
    FocusSessionLog,
    ObservationTimeline,
    ObservedSegment,
)
from mosemo.shared.domain.timezones import Timezone

PRECEDING_PAGE_SIZE = 32


@dataclass(frozen=True, kw_only=True)
class LoadedSource:
    """The snapshot with the time spent reading the source and building it."""

    snapshot: SourceSnapshot
    read_ms: float
    build_ms: float


def source_segment(observed: ObservedSegment) -> SourceSegment:
    if observed.kind is ActivityTimelineKind.CAPTURE_GAP:
        return SourceSegment(
            id=str(observed.id),
            first_event_id=str(observed.first_event_id),
            kind=SegmentKind.CAPTURE_GAP,
            started_at=observed.started_at,
            ended_at=observed.ended_at,
            observed_end=observed.ended_at,
        )
    return SourceSegment(
        id=str(observed.id),
        first_event_id=str(observed.first_event_id),
        kind=SegmentKind.ACTIVITY,
        started_at=observed.started_at,
        ended_at=observed.ended_at,
        observed_end=observed.ended_at or observed.last_observed_at,
        version=observed.version,
        context=observed.context,
    )


def source_session(view: CompletedFocusSessionView) -> CompletedFocusSession:
    return CompletedFocusSession(
        id=str(view.focus_session_id),
        started_at=view.started_at,
        ended_at=view.ended_at,
        label_id=str(view.label_id),
        pauses=view.pauses if view.pauses_reported else None,
    )


def order_key(segment: ObservedSegment) -> tuple[datetime, UUID]:
    """The timeline order: `started_at`, then segment ID."""
    return segment.started_at, segment.id


def is_open_gap(segment: ObservedSegment) -> bool:
    return segment.kind is ActivityTimelineKind.CAPTURE_GAP and segment.ended_at is None


def elapsed_ms(since: float) -> float:
    return round((perf_counter() - since) * 1000, 3)


def assemble(
    rows: Sequence[ObservedSegment],
    confirmations: Iterable[LabelConfirmationView],
    *,
    timezone: Timezone,
    sessions: Sequence[CompletedFocusSession],
    started: float,
) -> LoadedSource:
    """Build the snapshot from rows in timeline order; reading ended now."""
    read_ms = elapsed_ms(started)
    building = perf_counter()
    snapshot = SourceSnapshot(
        timezone=timezone,
        segments=tuple(source_segment(row) for row in rows),
        confirmations={
            str(item.first_event_id): ConfirmedLabel(
                segment_version=item.segment_version,
                label_id=None if item.label_id is None else str(item.label_id),
            )
            for item in confirmations
        },
        sessions=tuple(sessions),
    )
    return LoadedSource(
        snapshot=snapshot, read_ms=read_ms, build_ms=elapsed_ms(building)
    )


class SourceSnapshotLoader:
    """Reads inside the caller's transaction and never opens one itself.

    Every read uses the one `now` the caller passes in, so segment ends and
    versions agree across the reads. The daily stats batch uses it too.
    """

    def __init__(
        self,
        *,
        timeline: ObservationTimeline,
        focus_sessions: FocusSessionLog,
        confirmations: LabelConfirmationDirectory,
        resolver: UsageTargetResolver,
    ) -> None:
        self._timeline = timeline
        self._focus_sessions = focus_sessions
        self._confirmations = confirmations
        self._resolver = resolver

    async def load_day(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        timezone: Timezone,
        window: DayWindow,
        now: datetime,
    ) -> LoadedSource:
        """The date window, the windows of the completed sessions started on
        the date and the segment after the last activity ending on the date."""
        started = perf_counter()
        views = await self._focus_sessions.list_completed_started_between(
            db_session, account_id, start=window.start, end=window.end
        )
        sessions = tuple(source_session(view) for view in views)
        windows = [window.bounds]
        windows += [(session.started_at, session.ended_at) for session in sessions]
        rows: dict[UUID, ObservedSegment] = {}
        for bounds in windows:
            for row in await self._read_window(
                db_session, account_id, bounds=bounds, now=now
            ):
                rows[row.id] = row
        following = await self._following(
            db_session, account_id, rows=rows.values(), window=window, now=now
        )
        if following is not None:
            rows[following.id] = following
        ordered = sorted(rows.values(), key=order_key)
        confirmations = await self._read_confirmations(
            db_session, account_id, rows=ordered
        )
        return assemble(
            ordered,
            confirmations,
            timezone=timezone,
            sessions=sessions,
            started=started,
        )

    async def load_session(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        session: CompletedFocusSessionView,
        timezone: Timezone,
        now: datetime,
    ) -> LoadedSource:
        """The session window with its backward read; the snapshot holds only
        this session."""
        started = perf_counter()
        rows = await self._read_window(
            db_session,
            account_id,
            bounds=(session.started_at, session.ended_at),
            now=now,
        )
        confirmations = await self._read_confirmations(
            db_session, account_id, rows=rows
        )
        return assemble(
            rows,
            confirmations,
            timezone=timezone,
            sessions=(source_session(session),),
            started=started,
        )

    async def find_observed_through(
        self, db_session: AsyncSession, account_id: UUID
    ) -> datetime | None:
        """The account's last observation (FR-29), not the read time."""
        return await self._timeline.find_observed_through(db_session, account_id)

    async def _read_confirmations(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        rows: Sequence[ObservedSegment],
    ) -> list[LabelConfirmationView]:
        return await self._confirmations.find_by_first_events(
            db_session,
            account_id,
            first_event_ids=[
                row.first_event_id
                for row in rows
                if row.kind is not ActivityTimelineKind.CAPTURE_GAP
            ],
        )

    async def _read_window(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        bounds: Interval,
        now: datetime,
    ) -> list[ObservedSegment]:
        """D1 rows plus the D2 backward read from the first closed row."""
        start, end = bounds
        rows = await self._timeline.read_range(
            db_session, account_id, start=start, end=end, now=now
        )
        first = next((row for row in rows if not is_open_gap(row)), None)
        if first is None:
            return rows
        return [
            *await self._read_back(db_session, account_id, first=first, now=now),
            *rows,
        ]

    async def _read_back(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        first: ObservedSegment,
        now: datetime,
    ) -> list[ObservedSegment]:
        """Pages of earlier segments while the whole page continues the run.

        A page reads up to one microsecond after the cursor start (the stored
        precision) and keeps what comes before the cursor in timeline order, so
        segments starting at the same instant are not skipped.
        """
        backward = BackwardRead(self._resolver, first=source_segment(first))
        earlier: list[ObservedSegment] = []
        cursor = first
        while True:
            fetched = await self._timeline.list_preceding(
                db_session,
                account_id,
                before=cursor.started_at + MICROSECOND,
                limit=PRECEDING_PAGE_SIZE,
                now=now,
            )
            page = [row for row in fetched if order_key(row) < order_key(cursor)]
            earlier = page[::-1] + earlier
            segments = [source_segment(row) for row in earlier]
            if (
                len(fetched) < PRECEDING_PAGE_SIZE
                or not page
                or not backward.needs_more(segments)
            ):
                chained = len(backward.chain(segments))
                return earlier[len(earlier) - chained :]
            cursor = earlier[0]

    async def _following(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        rows: Iterable[ObservedSegment],
        window: DayWindow,
        now: datetime,
    ) -> ObservedSegment | None:
        """P7-B15: the segment after the last row when it is a closed activity
        ending on the date. Any earlier such row's next segment is already read."""
        last = max(rows, key=order_key, default=None)
        if last is None or not ends_inside(source_segment(last), window):
            return None
        return await self._timeline.find_following(
            db_session, account_id, segment=last, now=now
        )
