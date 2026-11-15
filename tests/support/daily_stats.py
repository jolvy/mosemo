"""A `DailyStats` with every part filled, for the analytics storage tests."""

from datetime import UTC, datetime

from mosemo.analytics.domain.daily_stats import (
    AppUsage,
    AuxiliaryValues,
    DailyStats,
    SessionTotals,
    TargetUsage,
    UsageTime,
)
from mosemo.analytics.domain.session_analysis import Distraction, TimeComposition
from mosemo.analytics.domain.transitions import LabelTransition, TargetTransition
from mosemo.analytics.domain.usage_targets import (
    UNKNOWN_APP,
    UsageTarget,
    UsageTargetKind,
)

AT = datetime(2026, 11, 1, 1, tzinfo=UTC)
EDITOR = UsageTarget(kind=UsageTargetKind.APP, key="com.example.editor")
SITE = UsageTarget(kind=UsageTargetKind.SITE, key="xn--vv4b11d.xn--3e0b707e")


def daily_stats() -> DailyStats:
    return DailyStats(
        usage=UsageTime(
            apps=(AppUsage(app="com.example.editor", name="Editor", us=9),),
            targets=(
                TargetUsage(target=EDITOR, us=6),
                TargetUsage(target=SITE, us=3),
            ),
            unknown_app_us=2,
            opaque_us=1,
            observed_us=12,
        ),
        target_transitions=(
            TargetTransition(at=AT, previous=EDITOR, next=SITE, previous_dwell_us=1),
            TargetTransition(
                at=AT, previous=SITE, next=EDITOR, previous_dwell_us=6_000_000
            ),
        ),
        label_transitions=(LabelTransition(at=AT, previous="a", next="b"),),
        sessions=SessionTotals(
            count=1,
            analysis_us=21,
            composition=TimeComposition(
                focus_us=1,
                lapse_us=2,
                unclassified_us=3,
                pending_us=4,
                opaque_us=5,
                unobserved_us=6,
                lapse_count=2,
            ),
            distractions=(
                Distraction(target=SITE, entries=2, lapse_us=2),
                Distraction(target=UNKNOWN_APP, entries=1, lapse_us=1),
            ),
        ),
        auxiliary=AuxiliaryValues(
            short_dwell_transition_count=1,
            browser_without_url_us=4,
            capture_gap_count=1,
            silence_count=2,
            pending_us=3,
        ),
    )
