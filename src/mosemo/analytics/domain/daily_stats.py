"""Daily stats: C1, C2, C7, totals of sessions started on the date, auxiliary values."""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from mosemo.analytics.domain.day_reading import ends_inside
from mosemo.analytics.domain.session_analysis import (
    Distraction,
    TimeComposition,
    measure,
    ranked_distractions,
)
from mosemo.analytics.domain.source import DayWindow, SourceSnapshot
from mosemo.analytics.domain.transitions import (
    LabelTransition,
    TargetTransition,
    label_transitions,
    target_transitions,
)
from mosemo.analytics.domain.usage_targets import (
    LapseTarget,
    UsageTarget,
    UsageTargetResolver,
)

SHORT_DWELL_US = 5_000_000


@dataclass(frozen=True, kw_only=True)
class AppUsage:
    app: str
    us: int


@dataclass(frozen=True, kw_only=True)
class TargetUsage:
    target: UsageTarget
    us: int


@dataclass(frozen=True, kw_only=True)
class UsageTime:
    """C1. Rankings follow FR-4: time descending, then key ascending."""

    apps: tuple[AppUsage, ...]
    targets: tuple[TargetUsage, ...]
    unknown_app_us: int
    opaque_us: int
    observed_us: int


@dataclass(frozen=True, kw_only=True)
class SessionTotals:
    """Totals of the completed sessions that started on the date (P7)."""

    count: int
    analysis_us: int
    composition: TimeComposition
    distractions: tuple[Distraction, ...]


@dataclass(frozen=True, kw_only=True)
class AuxiliaryValues:
    """The five auxiliary values (metrics spec 「결정 (2026-11-15)」)."""

    short_dwell_transition_count: int
    browser_without_url_us: int
    capture_gap_count: int
    silence_count: int
    pending_us: int


@dataclass(frozen=True, kw_only=True)
class DailyStats:
    usage: UsageTime
    target_transitions: tuple[TargetTransition, ...]
    label_transitions: tuple[LabelTransition, ...]
    sessions: SessionTotals
    auxiliary: AuxiliaryValues


class DailyStatsCalculator:
    """Calculates one date from a snapshot of the segments read for it."""

    def __init__(self, resolver: UsageTargetResolver) -> None:
        self._resolver = resolver

    def compute(self, snapshot: SourceSnapshot, window: DayWindow) -> DailyStats:
        c2 = tuple(
            item.transition
            for item in target_transitions(snapshot, self._resolver)
            if window.contains(item.transition.at)
        )
        return DailyStats(
            usage=self._usage(snapshot, window),
            target_transitions=c2,
            label_transitions=tuple(
                transition
                for transition in label_transitions(snapshot)
                if window.contains(transition.at)
            ),
            sessions=self._session_totals(snapshot, window),
            auxiliary=self._auxiliary(snapshot, window=window, c2=c2),
        )

    def _usage(self, snapshot: SourceSnapshot, window: DayWindow) -> UsageTime:
        apps: Counter[str] = Counter()
        targets: Counter[UsageTarget] = Counter()
        unknown = opaque = observed = 0
        for segment in snapshot.segments:
            if not segment.is_activity:
                continue
            us = segment.overlap_us(window.bounds)
            observed += us
            app = self._resolver.app(segment)
            target = self._resolver.target(segment)
            if segment.is_opaque:
                opaque += us
            elif app is None or target is None:
                unknown += us
            else:
                apps[app] += us
                targets[target] += us
        return UsageTime(
            apps=tuple(AppUsage(app=app, us=us) for app, us in ranked(apps, key=str)),
            targets=tuple(
                TargetUsage(target=target, us=us)
                for target, us in ranked(targets, key=lambda target: target.key)
            ),
            unknown_app_us=unknown,
            opaque_us=opaque,
            observed_us=observed,
        )

    def _session_totals(
        self, snapshot: SourceSnapshot, window: DayWindow
    ) -> SessionTotals:
        count = analysis_us = 0
        composition = TimeComposition()
        totals: dict[LapseTarget, list[int]] = {}
        for session in snapshot.sessions:
            if not window.contains(session.started_at):
                continue
            measured = measure(snapshot, session)
            count += 1
            analysis_us += measured.analysis_us
            composition = composition.plus(measured.composition())
            for target, (entries, lapse_us) in measured.distraction_totals(
                self._resolver
            ).items():
                row = totals.setdefault(target, [0, 0])
                row[0] += entries
                row[1] += lapse_us
        return SessionTotals(
            count=count,
            analysis_us=analysis_us,
            composition=composition,
            distractions=tuple(ranked_distractions(totals)),
        )

    def _auxiliary(
        self,
        snapshot: SourceSnapshot,
        *,
        window: DayWindow,
        c2: Sequence[TargetTransition],
    ) -> AuxiliaryValues:
        segments = snapshot.segments
        activities = [segment for segment in segments if segment.is_activity]
        return AuxiliaryValues(
            short_dwell_transition_count=sum(
                1 for row in c2 if row.previous_dwell_us < SHORT_DWELL_US
            ),
            browser_without_url_us=sum(
                segment.overlap_us(window.bounds)
                for segment in activities
                if self._resolver.is_browser_app(segment)
            ),
            capture_gap_count=sum(
                1
                for segment in segments
                if not segment.is_activity
                and segment.started_at < window.end
                and (segment.ended_at is None or segment.ended_at >= window.start)
            ),
            silence_count=sum(
                1
                for before, after in pairwise(segments)
                if ends_inside(before, window) and not before.adjoins(after)
            ),
            pending_us=sum(
                segment.overlap_us(window.bounds)
                for segment in activities
                if not segment.is_opaque and snapshot.confirmation(segment) is None
            ),
        )


def ranked[T](
    totals: Mapping[T, int], *, key: Callable[[T], str]
) -> list[tuple[T, int]]:
    """FR-4 order without zero entries."""
    return sorted(
        ((item, us) for item, us in totals.items() if us > 0),
        key=lambda pair: (-pair[1], key(pair[0])),
    )
