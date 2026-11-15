"""Analytics assembly for the assembly roots: mapping, handlers and PSL rules."""

from importlib.resources import files

from mosemo.analytics.application.marking import DailyStatsMarking
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository
from mosemo.analytics.infrastructure.tables import map_tables
from mosemo.identity.public import AccountDirectory
from mosemo.labeling.public import LabelConfirmed
from mosemo.observation.public import (
    FocusSessionCompleted,
    FocusSessionLog,
    ObservationTimeline,
    TimelineChanged,
)
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher

__all__ = ["map_tables", "public_suffix_rules", "register_handlers"]

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
