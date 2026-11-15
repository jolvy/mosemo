"""Analytics assembly for the assembly roots: mapping, handlers, PSL rules,
router and batch jobs."""

from dataclasses import dataclass
from datetime import timedelta
from importlib.resources import files
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.analytics.application.daily_stats_batch import (
    DailyStatsComputation,
    DailyStatsStore,
    RefreshDailyStats,
    RefreshSettings,
    WaitPoints,
)
from mosemo.analytics.application.definition_marking import (
    DefinitionMarkWriter,
    MarkOutdatedDefinition,
)
from mosemo.analytics.application.marking import DailyStatsMarking
from mosemo.analytics.application.reads import (
    GetDailyStats,
    GetFocusSessionAnalysis,
    StoredStatsLookup,
)
from mosemo.analytics.application.source_loader import SourceSnapshotLoader
from mosemo.analytics.domain.daily_stats import DailyStatsCalculator
from mosemo.analytics.domain.definition import CURRENT_DEFINITION
from mosemo.analytics.domain.session_analysis import SessionAnalyzer
from mosemo.analytics.domain.usage_targets import UsageTargetResolver
from mosemo.analytics.infrastructure.daily_stats import (
    DailyStatsRepository,
    OutdatedStatsQuery,
)
from mosemo.analytics.infrastructure.definitions import AnalyticsDefinitionQuery
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository
from mosemo.analytics.infrastructure.tables import map_tables
from mosemo.analytics.presentation.error_docs import ERROR_DOCS
from mosemo.analytics.presentation.router import create_router
from mosemo.identity.public import AccountDirectory
from mosemo.labeling.public import LabelConfirmationDirectory, LabelConfirmed
from mosemo.observation.public import (
    FocusSessionCompleted,
    FocusSessionLog,
    ObservationTimeline,
    TimelineChanged,
)
from mosemo.platform.scheduling import BatchJob, JobReport, every
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher

__all__ = [
    "DAILY_STATS_JOB_NAME",
    "DEFINITION_MARK_JOB_NAME",
    "ERROR_DOCS",
    "Analytics",
    "AnalyticsBatchConfig",
    "create_daily_stats_job",
    "create_definition_mark_job",
    "map_tables",
    "public_suffix_rules",
    "register_handlers",
]

PUBLIC_SUFFIX_LIST = "public_suffix_list.dat"
DAILY_STATS_JOB_NAME = "analytics-daily-stats"
DEFINITION_MARK_JOB_NAME = "analytics-definition-mark"
DAILY_STATS_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True, kw_only=True)
class AnalyticsBatchConfig:
    """Batch values the assembly root reads from its settings.

    `reprocess_per_run` caps the definition markers one run claims (0 stops
    definition reprocessing). The test wait points stay empty in production.
    """

    reprocess_per_run: int
    test_wait_points: frozenset[str] = frozenset()
    test_wait_directory: Path | None = None

    def settings(self) -> RefreshSettings:
        return RefreshSettings(
            reprocess_per_run=self.reprocess_per_run,
            wait_points=WaitPoints(
                names=self.test_wait_points, directory=self.test_wait_directory
            ),
        )


def register_handlers(
    dispatcher: EventDispatcher,
    *,
    timeline: ObservationTimeline,
    accounts: AccountDirectory,
    focus_sessions: FocusSessionLog,
    clock: Clock,
) -> None:
    """Subscribe the recompute marking to the source change events.

    The collaborators come from the assembly root because analytics bootstrap
    may not import other contexts' bootstraps.
    """
    marking = DailyStatsMarking(
        timeline=timeline,
        accounts=accounts,
        focus_sessions=focus_sessions,
        markers=RecomputeMarkerRepository(),
        clock=clock,
    )
    dispatcher.subscribe(TimelineChanged, marking.on_timeline_changed)
    dispatcher.subscribe(LabelConfirmed, marking.on_label_confirmed)
    dispatcher.subscribe(FocusSessionCompleted, marking.on_focus_session_completed)


def public_suffix_rules() -> str:
    """The bundled PSL snapshot text for `UsageTargetResolver` (never fetched live)."""
    resource = files("mosemo.analytics.domain").joinpath(PUBLIC_SUFFIX_LIST)
    return resource.read_text(encoding="utf-8")


class Analytics:
    """Stateless analytics read objects built once when an assembly root starts.

    The collaborators come from the assembly root (other contexts' bootstraps
    are not imported here).
    """

    def __init__(
        self,
        *,
        accounts: AccountDirectory,
        timeline: ObservationTimeline,
        focus_sessions: FocusSessionLog,
        confirmations: LabelConfirmationDirectory,
        clock: Clock,
    ) -> None:
        resolver = UsageTargetResolver(public_suffix_rules())
        self._accounts = accounts
        self.calculator = DailyStatsCalculator(resolver)
        self.source_loader = SourceSnapshotLoader(
            timeline=timeline,
            focus_sessions=focus_sessions,
            confirmations=confirmations,
            resolver=resolver,
        )
        self.get_daily_stats = GetDailyStats(
            accounts=accounts,
            loader=self.source_loader,
            calculator=self.calculator,
            stored_stats=StoredStatsLookup(
                markers=RecomputeMarkerRepository(),
                stored_stats=DailyStatsRepository(),
            ),
            clock=clock,
        )
        self.get_focus_session_analysis = GetFocusSessionAnalysis(
            accounts=accounts,
            focus_sessions=focus_sessions,
            loader=self.source_loader,
            analyzer=SessionAnalyzer(resolver),
            clock=clock,
        )

    def router(self) -> APIRouter:
        return create_router(
            get_daily_stats=self.get_daily_stats,
            get_focus_session_analysis=self.get_focus_session_analysis,
        )

    def refresh_daily_stats(
        self,
        *,
        settings: RefreshSettings,
        dispatcher: EventDispatcher,
        clock: Clock,
    ) -> RefreshDailyStats:
        """The batch use case over the same loader and calculator as the reads."""
        return RefreshDailyStats(
            store=DailyStatsStore(
                markers=RecomputeMarkerRepository(),
                stored_stats=DailyStatsRepository(),
                definitions=AnalyticsDefinitionQuery(),
                accounts=self._accounts,
                definition=CURRENT_DEFINITION,
            ),
            computation=DailyStatsComputation(
                accounts=self._accounts,
                loader=self.source_loader,
                calculator=self.calculator,
            ),
            dispatcher=dispatcher,
            clock=clock,
            settings=settings,
        )


def create_daily_stats_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    config: AnalyticsBatchConfig,
    analytics: Analytics,
    dispatcher: EventDispatcher,
    clock: Clock,
) -> BatchJob:
    """The daily stats batch: every five minutes and once on start."""
    refresh = analytics.refresh_daily_stats(
        settings=config.settings(), dispatcher=dispatcher, clock=clock
    )

    async def run() -> JobReport:
        async with session_factory() as db_session:
            report = await refresh.execute(db_session)
        return JobReport(fields=report.fields(), failed=report.failed)

    return BatchJob(
        DAILY_STATS_JOB_NAME, run, every(DAILY_STATS_INTERVAL, run_on_start=True)
    )


def create_definition_mark_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    timeline: ObservationTimeline,
    accounts: AccountDirectory,
    dispatcher: EventDispatcher,
    clock: Clock,
) -> BatchJob:
    """The definition reprocessing marks, run by hand (no schedule)."""
    mark = MarkOutdatedDefinition(
        writer=DefinitionMarkWriter(
            timeline=timeline,
            accounts=accounts,
            outdated=OutdatedStatsQuery(),
            markers=RecomputeMarkerRepository(),
            definition=CURRENT_DEFINITION,
        ),
        dispatcher=dispatcher,
        clock=clock,
    )

    async def run() -> JobReport:
        async with session_factory() as db_session:
            report = await mark.execute(db_session)
        return JobReport(fields=report.fields())

    return BatchJob(DEFINITION_MARK_JOB_NAME, run)
