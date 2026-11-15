"""The daily stats batch (ADR 0016 「일간 통계 프로토콜」·「실패·재처리」).

The batch claims due markers in a short transaction, reads each date's source
after the claim in a READ COMMITTED READ ONLY transaction, computes outside any
transaction, and saves with the conditional delete in one transaction that
first checks the definition and account timezone under FOR SHARE. No lock is
held while computing, so source writes never wait for the batch.
"""

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.application.source_loader import (
    LoadedSource,
    SourceSnapshotLoader,
    elapsed_ms,
)
from mosemo.analytics.domain.daily_stats import DailyStats, DailyStatsCalculator
from mosemo.analytics.domain.definition import AnalyticsDefinition
from mosemo.analytics.domain.markers import (
    RETRY_POLICY,
    MarkerKind,
    RecomputeMarker,
)
from mosemo.analytics.domain.source import DayWindow
from mosemo.analytics.domain.stored_stats import StoredDailyStats
from mosemo.analytics.infrastructure.daily_stats import DailyStatsRepository
from mosemo.analytics.infrastructure.definitions import AnalyticsDefinitionQuery
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository
from mosemo.identity.public import AccountDirectory
from mosemo.platform.database.transactions import (
    read_snapshot,
    reset_session,
    unit_of_work,
)
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.application.use_case import UseCase
from mosemo.shared.domain.timezones import Timezone

AFTER_CLAIM = "after_claim"
AFTER_SOURCE_READ = "after_source_read"
BEFORE_SAVE_COMMIT = "before_save_commit"
WAIT_POINT_NAMES = frozenset({AFTER_CLAIM, AFTER_SOURCE_READ, BEFORE_SAVE_COMMIT})

CLAIM_LIMIT = 50
RUN_BUDGET_SECONDS = 120.0
SOURCE_STATEMENT_TIMEOUT = "30s"
SAVE_LOCK_TIMEOUT = "3s"
WAIT_POLL_SECONDS = 0.01

# Date-level database errors: recorded on the marker, then the next date.
DATE_FAILURE_STATES = {
    "55P03": "lock_timeout",
    "57014": "statement_timeout",
    "40001": "serialization",
    "40P01": "serialization",
}
# Server shutdown and terminated backends end the run like a lost connection.
CONNECTION_STATES = frozenset({"57P01", "57P02", "57P03"})
COMPUTE_FAILURE = "compute"
CONNECTION = "connection"
DEFINITION = "definition"


@dataclass(frozen=True, kw_only=True)
class WaitPoints:
    """Test-only pauses between the batch steps, off unless names are given.

    At an enabled point the batch creates `{name}.reached` in `directory` and
    waits until the test creates `{name}.go`, which it removes before going on.
    """

    names: frozenset[str] = frozenset()
    directory: Path | None = None

    def __post_init__(self) -> None:
        unknown = self.names - WAIT_POINT_NAMES
        if unknown:
            raise ValueError(f"unknown wait points: {', '.join(sorted(unknown))}")
        if self.names and self.directory is None:
            raise ValueError("wait points need a directory")

    @property
    def enabled(self) -> bool:
        return bool(self.names)

    async def reach(self, name: str) -> None:
        if name not in self.names or self.directory is None:
            return
        go = self.directory / f"{name}.go"
        (self.directory / f"{name}.reached").touch()
        while True:
            if go.exists():
                break
            await asyncio.sleep(WAIT_POLL_SECONDS)
        go.unlink()


NO_WAIT_POINTS = WaitPoints()


@dataclass(frozen=True, kw_only=True)
class RefreshSettings:
    """`reprocess_per_run` caps the definition markers one run claims; 0 stops
    definition reprocessing."""

    reprocess_per_run: int
    wait_points: WaitPoints = NO_WAIT_POINTS

    def __post_init__(self) -> None:
        if self.reprocess_per_run < 0:
            raise ValueError("reprocess_per_run must not be negative")


@dataclass(kw_only=True)
class RefreshReport:
    """The run counters for the one log line (no account, date or values)."""

    saved: int = 0
    superseded: int = 0
    stale: int = 0
    failures: Counter[str] = field(default_factory=Counter)
    quarantined_new: int = 0
    definition_mismatch: int = 0
    aborted: str | None = None
    max_source_read_ms: float = 0.0
    max_compute_ms: float = 0.0

    @property
    def failed(self) -> bool:
        """A lost connection fails the run; the claimed dates wait for the lease."""
        return self.aborted == CONNECTION

    def fields(self) -> dict[str, object]:
        return {
            "saved": self.saved,
            "superseded": self.superseded,
            "stale": self.stale,
            "failed": sum(self.failures.values()),
            **{
                f"failed_{name}": self.failures[name]
                for name in (
                    COMPUTE_FAILURE,
                    *sorted(set(DATE_FAILURE_STATES.values())),
                )
            },
            "quarantined_new": self.quarantined_new,
            "definition_mismatch": self.definition_mismatch,
            "aborted": self.aborted,
            "max_source_read_ms": self.max_source_read_ms,
            "max_compute_ms": self.max_compute_ms,
        }


class DefinitionChangedError(Exception):
    """The definition row differs from the batch code's definition."""


class TimezoneChangedError(Exception):
    """The account timezone changed after the source read."""


class DateFailedError(Exception):
    """One date failed with `classification` (the exception type name)."""

    def __init__(self, category: str, classification: str) -> None:
        super().__init__(category)
        self.category = category
        self.classification = classification


class ConnectionLostError(Exception):
    """The database connection failed; the run ends."""


def sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


def driver_error_name(error: DBAPIError) -> str:
    """The driver exception type name (e.g. `LockNotAvailableError`)."""
    cause = error.orig.__cause__ if error.orig is not None else None
    return type(cause or error.orig or error).__name__


def is_connection_error(error: BaseException) -> bool:
    if isinstance(error, OSError):
        return True
    if not isinstance(error, DBAPIError):
        return False
    state = sqlstate(error) or ""
    return (
        error.connection_invalidated
        or isinstance(error, InterfaceError)
        or state.startswith("08")
        or state in CONNECTION_STATES
    )


def classify_database_error(error: DBAPIError | OSError) -> Exception:
    """`ConnectionLostError` or a `DateFailedError`; other errors stay as they are."""
    if is_connection_error(error):
        return ConnectionLostError()
    assert isinstance(error, DBAPIError)
    category = DATE_FAILURE_STATES.get(sqlstate(error) or "")
    if category is None:
        return error
    return DateFailedError(category, driver_error_name(error))


@dataclass(frozen=True, kw_only=True)
class SaveOutcome:
    written: bool
    deleted: bool


class DailyStatsStore:
    """Marker, stored row and definition steps inside the caller's transaction."""

    def __init__(
        self,
        *,
        markers: RecomputeMarkerRepository,
        stored_stats: DailyStatsRepository,
        definitions: AnalyticsDefinitionQuery,
        accounts: AccountDirectory,
        definition: AnalyticsDefinition,
    ) -> None:
        self._markers = markers
        self._stored_stats = stored_stats
        self._definitions = definitions
        self._accounts = accounts
        self.definition = definition

    async def definition_matches(self, db_session: AsyncSession) -> bool:
        return await self._definitions.read(db_session) == self.definition.version

    async def claim(
        self, db_session: AsyncSession, *, now: datetime, definition_limit: int
    ) -> list[RecomputeMarker]:
        return await self._markers.claim_due(
            db_session,
            now=now,
            limit=CLAIM_LIMIT,
            definition_limit=definition_limit,
            policy=RETRY_POLICY,
        )

    async def save(
        self,
        db_session: AsyncSession,
        marker: RecomputeMarker,
        *,
        timezone: Timezone,
        stats: DailyStats,
        now: datetime,
    ) -> SaveOutcome:
        """Check the definition and timezone under FOR SHARE, then save the
        result as `source_version` = the claimed version and delete the marker
        only while it still has that version.

        Raises `DefinitionChangedError` or `TimezoneChangedError` to roll back.
        """
        await db_session.execute(
            text(f"SET LOCAL lock_timeout = '{SAVE_LOCK_TIMEOUT}'")
        )
        if (
            await self._definitions.read_for_share(db_session)
            != self.definition.version
        ):
            raise DefinitionChangedError
        stored_timezone = await self._accounts.read_timezone_for_share(
            db_session, marker.account_id
        )
        if stored_timezone != timezone:
            raise TimezoneChangedError
        written = await self._stored_stats.save_if_newer(
            db_session,
            StoredDailyStats(
                account_id=marker.account_id,
                local_date=marker.local_date,
                source_version=marker.version,
                definition_version=self.definition.version,
                timezone=timezone,
                computed_at=now,
                stats=stats,
            ),
        )
        deleted = await self._markers.delete_if_version(db_session, marker.version)
        return SaveOutcome(written=written, deleted=deleted)

    async def record_failure(
        self,
        db_session: AsyncSession,
        marker: RecomputeMarker,
        *,
        classification: str,
        now: datetime,
    ) -> bool:
        marker.fail(classification, now=now, policy=RETRY_POLICY)
        return await self._markers.record_failure(db_session, marker)


class DailyStatsComputation:
    """Reads one marked date's source and computes its daily stats."""

    def __init__(
        self,
        *,
        accounts: AccountDirectory,
        loader: SourceSnapshotLoader,
        calculator: DailyStatsCalculator,
    ) -> None:
        self._accounts = accounts
        self._loader = loader
        self._calculator = calculator

    async def load(
        self, db_session: AsyncSession, marker: RecomputeMarker, *, now: datetime
    ) -> tuple[Timezone, DayWindow, LoadedSource]:
        """Inside the caller's transaction: the account timezone and the source."""
        await db_session.execute(
            text(f"SET LOCAL statement_timeout = '{SOURCE_STATEMENT_TIMEOUT}'")
        )
        timezone = await self._accounts.get_timezone(db_session, marker.account_id)
        window = DayWindow.of(timezone, marker.local_date)
        loaded = await self._loader.load_day(
            db_session, marker.account_id, timezone=timezone, window=window, now=now
        )
        return timezone, window, loaded

    def compute(self, loaded: LoadedSource, window: DayWindow) -> DailyStats:
        return self._calculator.compute(loaded.snapshot, window)


class RefreshDailyStats(UseCase):
    """Job `analytics-daily-stats`: compute the claimed past dates and save them."""

    def __init__(
        self,
        *,
        store: DailyStatsStore,
        computation: DailyStatsComputation,
        dispatcher: EventDispatcher,
        clock: Clock,
        settings: RefreshSettings,
    ) -> None:
        self._store = store
        self._computation = computation
        self._dispatcher = dispatcher
        self._clock = clock
        self._settings = settings

    async def execute(self, db_session: AsyncSession) -> RefreshReport:
        """Claim and process batches of due markers until none is left, the
        time budget (checked between dates) runs out, or the run is aborted.

        Every step uses the run's start time, so a marker is processed at
        most once per run.
        """
        report = RefreshReport()
        now = self._clock()
        started = perf_counter()
        async with read_snapshot(db_session):
            matches = await self._store.definition_matches(db_session)
        if not matches:
            report.definition_mismatch = 1
            return report
        reprocess_left = self._settings.reprocess_per_run
        while perf_counter() - started < RUN_BUDGET_SECONDS:
            claimed = await self._claim(db_session, now=now, limit=reprocess_left)
            if not claimed:
                break
            reprocess_left -= sum(
                1 for marker in claimed if marker.kind is MarkerKind.DEFINITION
            )
            await self._settings.wait_points.reach(AFTER_CLAIM)
            for marker in claimed:
                if perf_counter() - started >= RUN_BUDGET_SECONDS:
                    return report
                await self._process(db_session, marker, now=now, report=report)
                if report.aborted is not None:
                    return report
        return report

    async def _claim(
        self, db_session: AsyncSession, *, now: datetime, limit: int
    ) -> list[RecomputeMarker]:
        reset_session(db_session)
        async with unit_of_work(db_session, self._dispatcher):
            claimed = await self._store.claim(
                db_session, now=now, definition_limit=limit
            )
        reset_session(db_session)
        return claimed

    async def _process(
        self,
        db_session: AsyncSession,
        marker: RecomputeMarker,
        *,
        now: datetime,
        report: RefreshReport,
    ) -> None:
        try:
            await self._refresh(db_session, marker, now=now, report=report)
        except DateFailedError as failure:
            report.failures[failure.category] += 1
            await self._record_failure(
                db_session,
                marker,
                classification=failure.classification,
                now=now,
                report=report,
            )
        except ConnectionLostError:
            report.aborted = CONNECTION
        except DefinitionChangedError:
            report.aborted = DEFINITION
        except TimezoneChangedError:
            # The timezone change left new markers for these dates.
            pass

    async def _refresh(
        self,
        db_session: AsyncSession,
        marker: RecomputeMarker,
        *,
        now: datetime,
        report: RefreshReport,
    ) -> None:
        timezone, window, loaded = await self._read(db_session, marker, now=now)
        report.max_source_read_ms = max(report.max_source_read_ms, loaded.read_ms)
        await self._settings.wait_points.reach(AFTER_SOURCE_READ)
        computing = perf_counter()
        try:
            stats = self._computation.compute(loaded, window)
        except Exception as error:
            raise DateFailedError(COMPUTE_FAILURE, type(error).__name__) from error
        report.max_compute_ms = max(report.max_compute_ms, elapsed_ms(computing))
        outcome = await self._save(
            db_session, marker, timezone=timezone, stats=stats, now=now
        )
        if not outcome.written:
            report.stale += 1
        elif outcome.deleted:
            report.saved += 1
        else:
            report.superseded += 1

    async def _read(
        self, db_session: AsyncSession, marker: RecomputeMarker, *, now: datetime
    ) -> tuple[Timezone, DayWindow, LoadedSource]:
        try:
            async with read_snapshot(db_session):
                return await self._computation.load(db_session, marker, now=now)
        except (DBAPIError, OSError) as error:
            raise classify_database_error(error) from error
        finally:
            reset_session(db_session)

    async def _save(
        self,
        db_session: AsyncSession,
        marker: RecomputeMarker,
        *,
        timezone: Timezone,
        stats: DailyStats,
        now: datetime,
    ) -> SaveOutcome:
        try:
            async with unit_of_work(db_session, self._dispatcher):
                outcome = await self._store.save(
                    db_session, marker, timezone=timezone, stats=stats, now=now
                )
                await self._settings.wait_points.reach(BEFORE_SAVE_COMMIT)
        except (DBAPIError, OSError) as error:
            raise classify_database_error(error) from error
        finally:
            reset_session(db_session)
        return outcome

    async def _record_failure(
        self,
        db_session: AsyncSession,
        marker: RecomputeMarker,
        *,
        classification: str,
        now: datetime,
        report: RefreshReport,
    ) -> None:
        """Keep the backoff and class only while the claimed version stands."""
        try:
            async with unit_of_work(db_session, self._dispatcher):
                recorded = await self._store.record_failure(
                    db_session, marker, classification=classification, now=now
                )
        except (DBAPIError, OSError) as error:
            if is_connection_error(error):
                report.aborted = CONNECTION
                return
            raise
        finally:
            reset_session(db_session)
        if recorded and marker.is_quarantined(RETRY_POLICY):
            report.quarantined_new += 1
