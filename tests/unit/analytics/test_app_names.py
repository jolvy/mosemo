"""FR-1: an app shows the latest name observed for its bundleId in the period."""

from mosemo.analytics.domain.daily_stats import DailyStats, DailyStatsCalculator
from mosemo.analytics.domain.session_analysis import SessionAnalyzer
from tests.support.analytics_product import resolver, snapshot, window
from tests.support.analytics_timelines import (
    CODING,
    DAY,
    OPAQUE,
    PSL,
    activity,
    app,
    focus,
    timeline,
    unknown_app,
)


def named(bundle_id: str, name: str) -> dict:
    context = app(bundle_id)
    context["app"]["name"] = {"status": "captured", "value": name}
    return context


def unnamed(bundle_id: str) -> dict:
    context = app(bundle_id)
    context["app"]["name"] = {"status": "unavailable", "reason": "denied"}
    return context


def test_daily_app_names_are_the_latest_observed_name_on_the_date() -> None:
    data = timeline(
        activity("before", -10, 0, named("com.example.editor", "Old")),
        activity("a", 0, 10, named("com.example.editor", "Editor")),
        activity("b", 10, 20, named("com.example.editor", "Editor 2")),
        activity("c", 20, 30, unnamed("com.example.editor")),
        activity("d", 30, 40, unnamed("com.example.notes")),
        activity("e", 40, 50, unknown_app("Hidden")),
        activity("f", 50, 60, OPAQUE),
    )

    stats = DailyStatsCalculator(resolver(PSL)).compute(
        snapshot(data), window(data, DAY)
    )

    assert stats.app_names() == {
        "com.example.editor": "Editor 2",
        "com.example.notes": "com.example.notes",
    }


def test_session_app_names_come_from_the_session_pieces() -> None:
    data = timeline(
        activity("a", 0, 30, named("com.example.editor", "Editor")),
        activity("b", 30, 60, named("com.example.editor", "Editor 2")),
        activity("c", 60, 70, named("com.example.editor", "Later")),
        labels={"a": CODING, "b": CODING, "c": CODING},
        sessions=[focus(start=0, end=60)],
    )
    source = snapshot(data)

    analysis = SessionAnalyzer(resolver(PSL)).analyze(source, source.sessions[0])

    assert analysis.app_names == {"com.example.editor": "Editor 2"}


def test_empty_daily_stats_have_only_zeros_and_empty_lists() -> None:
    stats = DailyStats.empty()

    assert stats.usage.apps == stats.usage.targets == ()
    assert stats.target_transitions == stats.label_transitions == ()
    assert stats.sessions.count == stats.sessions.analysis_us == 0
    assert stats.auxiliary.capture_gap_count == stats.auxiliary.pending_us == 0
