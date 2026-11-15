"""Analytics reads over one REPEATABLE READ READ ONLY snapshot (ADR 0016 「분석 조회」).

A calculation error is not caught here: the request fails with the common 500
response (FR-30).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.application.source_loader import (
    LoadedSource,
    SourceSnapshotLoader,
    elapsed_ms,
)
from mosemo.analytics.domain.daily_stats import DailyStats, DailyStatsCalculator
from mosemo.analytics.domain.definition import CURRENT_DEFINITION
from mosemo.analytics.domain.markers import MarkScope
from mosemo.analytics.domain.session_analysis import SessionAnalysis, SessionAnalyzer
from mosemo.analytics.domain.source import CompletedFocusSession, DayWindow
from mosemo.analytics.domain.stats_source import DailyStatsSource
from mosemo.analytics.domain.stored_stats import StoredDailyStats
from mosemo.analytics.infrastructure.daily_stats import DailyStatsRepository
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository
from mosemo.identity.public import AccountDirectory
from mosemo.observation.public import FocusSessionLog, FocusSessionStatus
from mosemo.platform.database.transactions import consistent_snapshot
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.use_case import UseCase
from mosemo.shared.domain.timezones import Timezone

logger = logging.getLogger(__name__)


class AnalyticsFocusSessionNotFoundError(Exception):
    """The focus session does not exist or belongs to another account."""


class AnalyticsFocusSessionInProgressError(Exception):
    """The focus session has not been completed yet."""


@dataclass(frozen=True, kw_only=True)
class DailyStatsView:
    """One date's stats; `observed_through` is the account's last observation."""

    day: date
    observed_through: datetime | None
    definition_version: int
    timezone: Timezone
    stats: DailyStats


@dataclass(frozen=True, kw_only=True)
class FocusSessionAnalysisView:
    session: CompletedFocusSession
    observed_through: datetime | None
    definition_version: int
    timezone: Timezone
    analysis: SessionAnalysis


def log_read(
    operation: str,
    *,
    source: DailyStatsSource,
    loaded: LoadedSource | None,
    compute_ms: float,
) -> None:
    """One line per read without account, date or URL (docs/logging.md)."""
    logger.info(
        "analytics read",
        extra={
            "operation": operation,
            "source": source.value,
            "source_read_ms": loaded.read_ms if loaded else 0,
            "snapshot_build_ms": loaded.build_ms if loaded else 0,
            "compute_ms": compute_ms,
            "segment_count": len(loaded.snapshot.segments) if loaded else 0,
        },
    )


class StoredStatsLookup:
    """Reads a past date's marker and stored row inside the caller's transaction."""

    def __init__(
        self,
        *,
        markers: RecomputeMarkerRepository,
        stored_stats: DailyStatsRepository,
    ) -> None:
        self._markers = markers
        self._stored_stats = stored_stats

    async def find_usable(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        day: date,
        today: date,
        timezone: Timezone,
    ) -> StoredDailyStats | None:
        """The stored row when `DailyStatsSource.choose` picks it."""
        is_past = day < today
        marker = stored = None
        if is_past:
            marker = await self._markers.find(
                db_session, account_id=account_id, local_date=day
            )
            stored = await self._stored_stats.find(
                db_session, account_id=account_id, local_date=day
            )
        source = DailyStatsSource.choose(
            is_past=is_past,
            marker=marker,
            stored=stored,
            definition_version=CURRENT_DEFINITION.version,
            timezone=timezone,
        )
        return stored if source is DailyStatsSource.STORED else None


class GetDailyStats(UseCase):
    def __init__(
        self,
        *,
        accounts: AccountDirectory,
        loader: SourceSnapshotLoader,
        calculator: DailyStatsCalculator,
        stored_stats: StoredStatsLookup,
        clock: Clock,
    ) -> None:
        self._accounts = accounts
        self._loader = loader
        self._calculator = calculator
        self._stored_stats = stored_stats
        self._clock = clock

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, day: date
    ) -> DailyStatsView:
        """A future date is empty; a past date uses its stored row when current.

        Raises identity's `AccountNotFoundError`.
        """
        now = self._clock()
        loaded: LoadedSource | None = None
        compute_ms = 0.0
        async with consistent_snapshot(db_session):
            timezone = await self._accounts.get_timezone(db_session, account_id)
            today = MarkScope.date_of(timezone, now)
            source = DailyStatsSource.FUTURE
            stats = DailyStats.empty()
            definition_version = CURRENT_DEFINITION.version
            if day <= today:
                stored = await self._stored_stats.find_usable(
                    db_session, account_id, day=day, today=today, timezone=timezone
                )
                if stored is not None:
                    source = DailyStatsSource.STORED
                    stats = stored.stats
                    definition_version = stored.definition_version
                else:
                    source = DailyStatsSource.COMPUTED
                    window = DayWindow.of(timezone, day)
                    loaded = await self._loader.load_day(
                        db_session,
                        account_id,
                        timezone=timezone,
                        window=window,
                        now=now,
                    )
                    computing = perf_counter()
                    stats = self._calculator.compute(loaded.snapshot, window)
                    compute_ms = elapsed_ms(computing)
            observed_through = await self._loader.find_observed_through(
                db_session, account_id
            )
        log_read(
            "analytics_daily_stats",
            source=source,
            loaded=loaded,
            compute_ms=compute_ms,
        )
        return DailyStatsView(
            day=day,
            observed_through=observed_through,
            definition_version=definition_version,
            timezone=timezone,
            stats=stats,
        )


class GetFocusSessionAnalysis(UseCase):
    def __init__(
        self,
        *,
        accounts: AccountDirectory,
        focus_sessions: FocusSessionLog,
        loader: SourceSnapshotLoader,
        analyzer: SessionAnalyzer,
        clock: Clock,
    ) -> None:
        self._accounts = accounts
        self._focus_sessions = focus_sessions
        self._loader = loader
        self._analyzer = analyzer
        self._clock = clock

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, focus_session_id: UUID
    ) -> FocusSessionAnalysisView:
        """Raises `AnalyticsFocusSessionNotFoundError`,
        `AnalyticsFocusSessionInProgressError` and identity's
        `AccountNotFoundError`."""
        now = self._clock()
        async with consistent_snapshot(db_session):
            timezone = await self._accounts.get_timezone(db_session, account_id)
            status = await self._focus_sessions.find_status(
                db_session, account_id, focus_session_id=focus_session_id
            )
            if status is FocusSessionStatus.MISSING:
                raise AnalyticsFocusSessionNotFoundError
            if status is FocusSessionStatus.IN_PROGRESS:
                raise AnalyticsFocusSessionInProgressError
            view = await self._focus_sessions.find_completed(
                db_session, account_id, focus_session_id=focus_session_id
            )
            assert view is not None
            loaded = await self._loader.load_session(
                db_session, account_id, session=view, timezone=timezone, now=now
            )
            session = loaded.snapshot.sessions[0]
            computing = perf_counter()
            analysis = self._analyzer.analyze(loaded.snapshot, session)
            compute_ms = elapsed_ms(computing)
            observed_through = await self._loader.find_observed_through(
                db_session, account_id
            )
        log_read(
            "analytics_focus_session_analysis",
            source=DailyStatsSource.COMPUTED,
            loaded=loaded,
            compute_ms=compute_ms,
        )
        return FocusSessionAnalysisView(
            session=session,
            observed_through=observed_through,
            definition_version=CURRENT_DEFINITION.version,
            timezone=timezone,
            analysis=analysis,
        )
