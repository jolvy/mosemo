"""SC-8: 생성 전략 입력에서 제품 계산의 불변식 E1·E3·E4·E5·E6·E8·E10이 성립한다."""

from datetime import date
from itertools import pairwise

from hypothesis import given

from mosemo.analytics.domain.daily_stats import DailyStats, DailyStatsCalculator
from mosemo.analytics.domain.session_analysis import (
    SessionAnalysis,
    SessionAnalyzer,
    TimeComposition,
)
from mosemo.analytics.domain.source import DayWindow, SourceSnapshot
from tests.support.analytics_product import flatten, resolver
from tests.support.analytics_strategies import (
    OPAQUE_SECRET,
    PSL,
    GeneratedTimeline,
    timelines,
)


def windows(timeline: GeneratedTimeline) -> list[DayWindow]:
    return [
        DayWindow.of(timeline.snapshot.timezone, date.fromisoformat(day))
        for day in timeline.days
    ]


def daily_of(snapshot: SourceSnapshot, window: DayWindow) -> DailyStats:
    return DailyStatsCalculator(resolver(PSL)).compute(snapshot, window)


def sessions_of(timeline: GeneratedTimeline) -> list[SessionAnalysis]:
    analyzer = SessionAnalyzer(resolver(PSL))
    return [
        analyzer.analyze(timeline.snapshot, session)
        for session in timeline.snapshot.sessions
    ]


def six_times(composition: TimeComposition) -> int:
    return (
        composition.focus_us
        + composition.lapse_us
        + composition.unclassified_us
        + composition.pending_us
        + composition.opaque_us
        + composition.unobserved_us
    )


@given(timelines())
def test_e1_rankings_sum_to_identified_observed_time(
    timeline: GeneratedTimeline,
) -> None:
    for window in windows(timeline):
        usage = daily_of(timeline.snapshot, window).usage
        identified = usage.observed_us - usage.unknown_app_us - usage.opaque_us

        assert sum(row.us for row in usage.apps) == identified
        assert sum(row.us for row in usage.targets) == identified


@given(timelines())
def test_e3_e4_e6_session_composition(timeline: GeneratedTimeline) -> None:
    for analysis in sessions_of(timeline):
        composition = analysis.composition

        assert six_times(composition) == analysis.analysis_us
        assert sum(row.lapse_us for row in analysis.distractions) == (
            composition.lapse_us
        )
        assert sum(row.entries for row in analysis.distractions) >= (
            composition.lapse_count
        )
        assert len(analysis.lapses) == composition.lapse_count


@given(timelines())
def test_e3_e4_daily_session_totals(timeline: GeneratedTimeline) -> None:
    for window in windows(timeline):
        totals = daily_of(timeline.snapshot, window).sessions

        assert six_times(totals.composition) == totals.analysis_us
        assert sum(row.lapse_us for row in totals.distractions) == (
            totals.composition.lapse_us
        )
        assert sum(row.entries for row in totals.distractions) >= (
            totals.composition.lapse_count
        )


@given(timelines())
def test_daily_session_totals_add_up_sessions_started_on_the_day(
    timeline: GeneratedTimeline,
) -> None:
    analyses = dict(zip(timeline.snapshot.sessions, sessions_of(timeline), strict=True))
    for window in windows(timeline):
        started = [a for s, a in analyses.items() if window.contains(s.started_at)]
        totals = daily_of(timeline.snapshot, window).sessions

        assert totals.count == len(started)
        assert totals.analysis_us == sum(a.analysis_us for a in started)
        assert totals.composition == fold(started)


def fold(analyses: list[SessionAnalysis]) -> TimeComposition:
    composition = TimeComposition()
    for analysis in analyses:
        composition = composition.plus(analysis.composition)
    return composition


@given(timelines())
def test_e5_session_transitions_are_daily_transitions(
    timeline: GeneratedTimeline,
) -> None:
    dailies = [daily_of(timeline.snapshot, window) for window in windows(timeline)]
    c2 = [row for stats in dailies for row in stats.target_transitions]
    c7 = [row for stats in dailies for row in stats.label_transitions]
    for analysis in sessions_of(timeline):
        assert all(row in c2 for row in analysis.target_transitions)
        assert all(row in c7 for row in analysis.label_transitions)


@given(timelines())
def test_e8_opaque_values_appear_in_no_output(timeline: GeneratedTimeline) -> None:
    outputs: list[object] = [
        daily_of(timeline.snapshot, window) for window in windows(timeline)
    ]
    outputs += sessions_of(timeline)

    assert not any(OPAQUE_SECRET in str(leaf) for leaf in flatten(outputs))


@given(timelines())
def test_e10_transitions_and_lapses_come_from_adjoining_activity(
    timeline: GeneratedTimeline,
) -> None:
    segments = timeline.snapshot.segments
    entered = {
        after.started_at
        for before, after in pairwise(segments)
        if before.adjoins(after) and before.is_activity and after.is_activity
    }
    activity_ids = {segment.id for segment in segments if segment.is_activity}
    for window in windows(timeline):
        stats = daily_of(timeline.snapshot, window)

        assert {row.at for row in stats.target_transitions} <= entered
        assert {row.at for row in stats.label_transitions} <= entered
        assert stats.usage.observed_us == sum(
            segment.overlap_us(window.bounds)
            for segment in segments
            if segment.is_activity
        )
    for analysis in sessions_of(timeline):
        assert {row.entry_segment_id for row in analysis.lapses} <= activity_ids


@given(timelines())
def test_e10_gaps_turned_into_silences_change_only_gap_counts(
    timeline: GeneratedTimeline,
) -> None:
    source = timeline.snapshot
    without_gaps = source.with_segments(
        tuple(
            segment
            for segment in source.segments
            if segment.is_activity
            or (segment.ended_at is not None and segment.length_us == 0)
        )
    )
    for window in windows(timeline):
        before, after = daily_of(source, window), daily_of(without_gaps, window)

        assert after.usage == before.usage
        assert after.target_transitions == before.target_transitions
        assert after.label_transitions == before.label_transitions
        assert after.sessions == before.sessions
        assert after.auxiliary.pending_us == before.auxiliary.pending_us
