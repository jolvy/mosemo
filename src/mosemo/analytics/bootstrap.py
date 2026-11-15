"""Analytics assembly for the assembly roots: mapping, handlers, PSL rules, router."""

from importlib.resources import files

from fastapi import APIRouter

from mosemo.analytics.application.marking import DailyStatsMarking
from mosemo.analytics.application.reads import (
    GetDailyStats,
    GetFocusSessionAnalysis,
    StoredStatsLookup,
)
from mosemo.analytics.application.source_loader import SourceSnapshotLoader
from mosemo.analytics.domain.daily_stats import DailyStatsCalculator
from mosemo.analytics.domain.session_analysis import SessionAnalyzer
from mosemo.analytics.domain.usage_targets import UsageTargetResolver
from mosemo.analytics.infrastructure.daily_stats import DailyStatsRepository
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
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher

__all__ = [
    "ERROR_DOCS",
    "Analytics",
    "map_tables",
    "public_suffix_rules",
    "register_handlers",
]

PUBLIC_SUFFIX_LIST = "public_suffix_list.dat"


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
        self.source_loader = SourceSnapshotLoader(
            timeline=timeline,
            focus_sessions=focus_sessions,
            confirmations=confirmations,
            resolver=resolver,
        )
        self.get_daily_stats = GetDailyStats(
            accounts=accounts,
            loader=self.source_loader,
            calculator=DailyStatsCalculator(resolver),
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
