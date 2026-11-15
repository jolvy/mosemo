"""The daily stats batch against a database of its own (TX·FI·API tests).

The claim picks due markers of every account, so the batch tests run in a
database no other test module writes to and empty it after each test. Times
use `tests.support.analytics_sources`: minutes from 2026-11-03 00:00 Seoul.
"""

import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from mosemo.analytics.application.daily_stats_batch import (
    DailyStatsComputation,
    DailyStatsStore,
    RefreshDailyStats,
    RefreshReport,
    RefreshSettings,
    WaitPoints,
)
from mosemo.analytics.application.definition_marking import (
    DefinitionMarkWriter,
    MarkOutdatedDefinition,
    MarkReport,
)
from mosemo.analytics.bootstrap import Analytics, public_suffix_rules, register_handlers
from mosemo.analytics.domain.daily_stats import DailyStats, DailyStatsCalculator
from mosemo.analytics.domain.definition import CURRENT_DEFINITION, AnalyticsDefinition
from mosemo.analytics.domain.markers import MarkScope
from mosemo.analytics.domain.source import DayWindow, SourceSnapshot
from mosemo.analytics.domain.stored_stats import StoredDailyStats
from mosemo.analytics.domain.usage_targets import UsageTargetResolver
from mosemo.analytics.infrastructure.daily_stats import (
    DailyStatsRepository,
    OutdatedStatsQuery,
)
from mosemo.analytics.infrastructure.definitions import AnalyticsDefinitionQuery
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository
from mosemo.analytics.infrastructure.tables import (
    analytics_daily_markers,
    analytics_definitions,
)
from mosemo.identity.bootstrap import create_account_directory
from mosemo.labeling.bootstrap import create_label_confirmation_directory
from mosemo.observation.bootstrap import (
    create_focus_session_log,
    create_observation_timeline,
)
from mosemo.platform.database.transactions import unit_of_work
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.domain.timezones import Timezone
from tests.support.analytics_sources import SourceSeed, at, detailed
from tests.support.clock import FakeClock

ROOT = Path(__file__).resolve().parents[2]
SEOUL = Timezone.ASIA_SEOUL
DAY = date(2026, 11, 3)
# 2026-11-10 00:00 Seoul: 11-03 is a past date and its markers are eligible.
NOW = at(7 * 1440)
EDITOR = detailed("com.example.editor", name="Editor")
NOTES = detailed("com.example.notes", name="Notes")
TIMEOUT = 20.0
BATCH_MODULE = "tests.support.processes.analytics_batch"


def day_minutes(day: date) -> float:
    """Minutes from 11-03 00:00 Seoul to `day` 00:00 Seoul."""
    return (day - DAY).days * 1440


def create_database(admin_url: str, name: str) -> str:
    """A new migrated database in the container behind `admin_url`."""

    async def create() -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
        finally:
            await engine.dispose()

    asyncio.run(create())
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return url


class FailingCalculator(DailyStatsCalculator):
    """Raises on the given dates (a deterministic calculation failure)."""

    def __init__(self, *, fail_on: Iterable[date]) -> None:
        super().__init__(UsageTargetResolver(public_suffix_rules()))
        self.fail_on = set(fail_on)

    def compute(self, snapshot: SourceSnapshot, window: DayWindow) -> DailyStats:
        if MarkScope.date_of(snapshot.timezone, window.start) in self.fail_on:
            raise ArithmeticError("calculation failed")
        return super().compute(snapshot, window)


class Gate:
    """The test side of the batch wait points (signal files in `directory`)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def wait_points(self, *names: str) -> WaitPoints:
        return WaitPoints(names=frozenset(names), directory=self.directory)

    async def reached(self, name: str, *, timeout: float = TIMEOUT) -> None:
        signal_file = self.directory / f"{name}.reached"
        async with asyncio.timeout(timeout):
            while not signal_file.exists():
                await asyncio.sleep(0.01)
        signal_file.unlink()

    def release(self, name: str) -> None:
        (self.directory / f"{name}.go").touch()


@asynccontextmanager
async def background[T](coroutine: Awaitable[T]) -> AsyncIterator[asyncio.Task[T]]:
    """Run `coroutine` as a task that is cancelled if the block leaves early."""
    task = asyncio.ensure_future(coroutine)
    try:
        yield task
    finally:
        if not task.done():
            task.cancel()
            with suppress(BaseException):
                await task


class BatchWorld:
    """The batch database, a fixed clock and the analytics objects."""

    def __init__(self, url: str, directory: Path) -> None:
        self.url = url
        # A short connect timeout: a stopped database may hang a connect (FI-7).
        self.engine: AsyncEngine = create_async_engine(
            url, pool_pre_ping=True, connect_args={"timeout": 5}
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.clock = FakeClock(NOW)
        self.gate = Gate(directory)
        self.dispatcher = EventDispatcher()
        register_handlers(
            self.dispatcher,
            timeline=create_observation_timeline(),
            accounts=create_account_directory(),
            focus_sessions=create_focus_session_log(),
            clock=self.clock,
        )
        self.analytics = Analytics(
            accounts=create_account_directory(),
            timeline=create_observation_timeline(),
            focus_sessions=create_focus_session_log(),
            confirmations=create_label_confirmation_directory(),
            clock=self.clock,
        )
        self.markers = RecomputeMarkerRepository()
        self.stats = DailyStatsRepository()

    async def reset(self) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(text("TRUNCATE accounts CASCADE"))
            await connection.execute(
                update(analytics_definitions).values(version=CURRENT_DEFINITION.version)
            )
        await self.engine.dispose()

    # -- the use cases ---------------------------------------------------------

    def refresh(
        self,
        *,
        definition: AnalyticsDefinition = CURRENT_DEFINITION,
        wait: Iterable[str] = (),
        reprocess_per_run: int = 600,
        calculator: DailyStatsCalculator | None = None,
        loader: Any = None,
        stored_stats: DailyStatsRepository | None = None,
    ) -> RefreshDailyStats:
        accounts = create_account_directory()
        return RefreshDailyStats(
            store=DailyStatsStore(
                markers=self.markers,
                stored_stats=stored_stats or self.stats,
                definitions=AnalyticsDefinitionQuery(),
                accounts=accounts,
                definition=definition,
            ),
            computation=DailyStatsComputation(
                accounts=accounts,
                loader=loader or self.analytics.source_loader,
                calculator=calculator or self.analytics.calculator,
            ),
            dispatcher=self.dispatcher,
            clock=self.clock,
            settings=RefreshSettings(
                reprocess_per_run=reprocess_per_run,
                wait_points=self.gate.wait_points(*wait),
            ),
        )

    async def run(self, refresh: RefreshDailyStats | None = None) -> RefreshReport:
        async with self.factory() as session:
            return await (refresh or self.refresh()).execute(session)

    def mark_use_case(
        self,
        *,
        definition: AnalyticsDefinition,
        markers: RecomputeMarkerRepository | None = None,
    ) -> MarkOutdatedDefinition:
        return MarkOutdatedDefinition(
            writer=DefinitionMarkWriter(
                timeline=create_observation_timeline(),
                accounts=create_account_directory(),
                outdated=OutdatedStatsQuery(),
                markers=markers or self.markers,
                definition=definition,
            ),
            dispatcher=self.dispatcher,
            clock=self.clock,
        )

    async def mark(
        self,
        *,
        definition: AnalyticsDefinition,
        markers: RecomputeMarkerRepository | None = None,
    ) -> MarkReport:
        async with self.factory() as session:
            return await self.mark_use_case(
                definition=definition, markers=markers
            ).execute(session)

    async def read(self, account_id: UUID, day: date = DAY) -> Any:
        async with self.factory() as session:
            return await self.analytics.get_daily_stats.execute(
                session, account_id=account_id, day=day
            )

    # -- seeding and source writes ---------------------------------------------

    async def account(self) -> SourceSeed:
        async with self.factory() as session:
            seed = SourceSeed(session)
            await seed.create_account()
            await session.commit()
        return seed

    @asynccontextmanager
    async def seeding(self, seed: SourceSeed) -> AsyncIterator[SourceSeed]:
        async with self.factory() as session:
            seed.session = session
            yield seed
            await seed.flush()
            await session.commit()

    async def write_source(
        self,
        seed: SourceSeed,
        *,
        change: Callable[[SourceSeed], object] | None = None,
        day: date = DAY,
        written: asyncio.Event | None = None,
        hold: asyncio.Event | None = None,
        timezone: Timezone = SEOUL,
    ) -> int:
        """A source write as the handlers do it: the account lock (3s
        `lock_timeout`), the change, then the marker in the same transaction.
        `written` is set after the marker upsert; `hold` delays the commit.
        Returns the marker version it wrote."""
        async with (
            self.factory() as session,
            unit_of_work(session, self.dispatcher),
        ):
            await create_observation_timeline().lock(session, seed.account_id)
            if change is not None:
                seed.session = session
                change(seed)
                await seed.flush()
            await self.markers.upsert_source(
                session,
                account_id=seed.account_id,
                local_date=day,
                eligible_at=MarkScope.eligible_at(day, timezone),
                now=self.clock(),
            )
            version = await session.scalar(
                select(analytics_daily_markers.c.version).where(
                    analytics_daily_markers.c.account_id == seed.account_id,
                    analytics_daily_markers.c.local_date == day,
                )
            )
            if written is not None:
                written.set()
            if hold is not None:
                await hold.wait()
        assert version is not None
        return version

    async def mark_days(
        self, seed: SourceSeed, *days: date, timezone: Timezone = SEOUL
    ) -> None:
        for day in days or (DAY,):
            await self.write_source(seed, day=day, timezone=timezone)

    async def save_definition_row(
        self,
        seed: SourceSeed,
        day: date = DAY,
        *,
        definition_version: int,
        source_version: int,
    ) -> None:
        """A stored row of `definition_version` computed from the source now."""
        stats = await self.computed(seed.account_id, day)
        async with self.factory() as session:
            await self.stats.save_if_newer(
                session,
                StoredDailyStats(
                    account_id=seed.account_id,
                    local_date=day,
                    source_version=source_version,
                    definition_version=definition_version,
                    timezone=SEOUL,
                    computed_at=self.clock(),
                    stats=stats,
                ),
            )
            await session.commit()

    async def set_definition_row(self, version: int) -> None:
        async with self.factory() as session:
            await session.execute(update(analytics_definitions).values(version=version))
            await session.commit()

    # -- what the tests check --------------------------------------------------

    async def computed(
        self, account_id: UUID, day: date = DAY, *, timezone: Timezone = SEOUL
    ) -> DailyStats:
        """The date computed from the committed source now."""
        async with self.factory() as session:
            window = DayWindow.of(timezone, day)
            loaded = await self.analytics.source_loader.load_day(
                session, account_id, timezone=timezone, window=window, now=self.clock()
            )
            await session.commit()
        return self.analytics.calculator.compute(loaded.snapshot, window)

    async def stored(
        self, account_id: UUID, day: date = DAY
    ) -> StoredDailyStats | None:
        async with self.factory() as session:
            found = await self.stats.find(
                session, account_id=account_id, local_date=day
            )
            await session.commit()
        return found

    async def marker_rows(self, account_id: UUID | None = None) -> list[dict[str, Any]]:
        statement = select(analytics_daily_markers).order_by(
            analytics_daily_markers.c.account_id, analytics_daily_markers.c.local_date
        )
        if account_id is not None:
            statement = statement.where(
                analytics_daily_markers.c.account_id == account_id
            )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [dict(row) for row in rows]

    async def marker(self, account_id: UUID, day: date = DAY) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in await self.marker_rows(account_id)
                if row["local_date"] == day
            ),
            None,
        )

    async def existing_marker(
        self, account_id: UUID, day: date = DAY
    ) -> dict[str, Any]:
        found = await self.marker(account_id, day)
        assert found is not None
        return found

    async def make_due(self) -> None:
        """The operator connection ends every lease and backoff now."""
        async with self.engine.begin() as connection:
            await connection.execute(
                update(analytics_daily_markers).values(
                    next_attempt_at=self.clock() - timedelta(seconds=1)
                )
            )

    async def lock_waiters(self) -> int:
        async with self.engine.connect() as connection:
            return await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )

    async def wait_for_lock_waiter(self, *, timeout: float = TIMEOUT) -> None:
        async with asyncio.timeout(timeout):
            while await self.lock_waiters() == 0:
                await asyncio.sleep(0.01)

    async def other_backends(self) -> list[dict[str, Any]]:
        """Backends of this database other than the asking connection."""
        async with self.engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT pid, state, wait_event_type FROM pg_stat_activity"
                    " WHERE datname = current_database() AND pid <> pg_backend_pid()"
                    " AND backend_type = 'client backend'"
                )
            )
            return [dict(row) for row in rows.mappings()]

    async def held_locks(self) -> int:
        """Advisory and row-level locks granted to other backends."""
        async with self.engine.connect() as connection:
            return await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_locks l"
                    " JOIN pg_stat_activity a ON a.pid = l.pid"
                    " WHERE a.datname = current_database()"
                    " AND l.pid <> pg_backend_pid()"
                    " AND (l.locktype = 'advisory' OR l.mode LIKE '%Row%')"
                )
            )

    async def wait_until_quiet(self, *, timeout: float = TIMEOUT) -> None:
        """No backend in a transaction and no lock left by another process."""
        async with asyncio.timeout(timeout):
            while (
                any(
                    row["state"] not in ("idle", None)
                    for row in await self.other_backends()
                )
                or await self.held_locks()
            ):
                await asyncio.sleep(0.05)

    async def close(self) -> None:
        await self.engine.dispose()


def definition(version: int) -> AnalyticsDefinition:
    return replace(CURRENT_DEFINITION, version=version)


def later_activity(minute: float, context: dict[str, Any] = NOTES) -> Callable:
    """A source change: a ten-minute activity at `minute` on 11-03."""
    return lambda seed: seed.activity(minute, minute + 10, context)


class BatchProcess:
    """The real batch process (`python -m mosemo.entrypoints.batch`) on the
    batch database, through `tests.support.processes.analytics_batch`."""

    def __init__(self, url: str, directory: Path) -> None:
        self.url = make_url(url)
        self.directory = directory

    def environment(self, **values: str) -> dict[str, str]:
        environment = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("DB_", "ANALYTICS_", "TEST_ANALYTICS_"))
        }
        environment.update(
            MOSEMO_ENV="test",
            LABEL_MODEL="openai:fake-model",
            DB_DATABASE=str(self.url.database),
            DB_USER=str(self.url.username),
            DB_PASSWORD=str(self.url.password),
            DB_HOST=str(self.url.host),
            DB_PORT=str(self.url.port),
            ANALYTICS_TEST_WAIT_DIRECTORY=str(self.directory),
        )
        environment.update(values)
        return environment

    def start(self, *arguments: str, **values: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "-m", BATCH_MODULE, *arguments],
            cwd=ROOT,
            env=self.environment(**values),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def run(self, *arguments: str, **values: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", BATCH_MODULE, *arguments],
            cwd=ROOT,
            env=self.environment(**values),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )


async def finish(process: subprocess.Popen[str], *, timeout: float = TIMEOUT) -> int:
    async with asyncio.timeout(timeout):
        while process.poll() is None:
            await asyncio.sleep(0.02)
    return process.returncode


async def kill(process: subprocess.Popen[str], sig: int = signal.SIGKILL) -> None:
    process.send_signal(sig)
    await finish(process)


def unique_name() -> str:
    return f"analytics_batch_{uuid4().hex[:8]}"


def kst(day: date, hour: int, minute: int = 0) -> datetime:
    return at(day_minutes(day) + hour * 60 + minute)
