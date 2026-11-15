from collections.abc import Mapping, Sequence
from uuid import UUID

from mosemo.analytics.application.reads import (
    DailyStatsView,
    FocusSessionAnalysisView,
)
from mosemo.analytics.domain.daily_stats import short_dwell_count
from mosemo.analytics.domain.session_analysis import (
    Distraction,
    LapseEvidence,
    TimeComposition,
)
from mosemo.analytics.domain.transitions import LabelTransition, TargetTransition
from mosemo.analytics.domain.usage_targets import (
    LapseTarget,
    UsageTarget,
    UsageTargetKind,
)
from mosemo.analytics.presentation.schemas import (
    AnalysisBasisResponse,
    AppUsageResponse,
    CollectionResponse,
    CompositionResponse,
    DailyStatsResponse,
    DistractionResponse,
    FocusSessionAnalysisResponse,
    LabelTransitionResponse,
    LabelTransitionsResponse,
    LapseEntryResponse,
    LapseResponse,
    PauseResponse,
    SessionTotalsResponse,
    TargetTransitionResponse,
    TargetTransitionsResponse,
    TargetUsageResponse,
    UsageResponse,
    UsageTargetResponse,
)
from mosemo.observation.public import DetailedActivityContext
from mosemo.shared.domain.timezones import Timezone


class AnalyticsMapper:
    """Analytics results → responses.

    An app shows its latest observed name from `app_names`, else its bundleId;
    a site shows its Unicode host. Opaque activity never reaches a result row.
    """

    def to_daily_stats_response(self, view: DailyStatsView) -> DailyStatsResponse:
        stats = view.stats
        names = stats.app_names()
        usage = stats.usage
        auxiliary = stats.auxiliary
        sessions = stats.sessions
        return DailyStatsResponse(
            date=view.day,
            observed_through=view.observed_through,
            basis=basis(view.definition_version, view.timezone),
            usage=UsageResponse(
                apps=[
                    AppUsageResponse(
                        bundle_id=row.app, display_name=row.name, duration_us=row.us
                    )
                    for row in usage.apps
                ],
                targets=[
                    TargetUsageResponse(
                        target=target_response(row.target, names), duration_us=row.us
                    )
                    for row in usage.targets
                ],
                unknown_app_us=usage.unknown_app_us,
                opaque_us=usage.opaque_us,
                observed_us=usage.observed_us,
                browser_without_url_us=auxiliary.browser_without_url_us,
            ),
            target_transitions=target_transitions_response(
                stats.target_transitions, names
            ),
            label_transitions=label_transitions_response(
                stats.label_transitions, pending_us=auxiliary.pending_us
            ),
            collection=CollectionResponse(
                capture_gap_count=auxiliary.capture_gap_count,
                silence_count=auxiliary.silence_count,
            ),
            sessions=SessionTotalsResponse(
                count=sessions.count,
                composition=composition_response(
                    sessions.composition, analysis_us=sessions.analysis_us
                ),
                distractions=[
                    distraction_response(row, names) for row in sessions.distractions
                ],
            ),
        )

    def to_focus_session_response(
        self, view: FocusSessionAnalysisView
    ) -> FocusSessionAnalysisResponse:
        session = view.session
        analysis = view.analysis
        names = analysis.app_names
        return FocusSessionAnalysisResponse(
            focus_session_id=UUID(session.id),
            label_id=UUID(session.label_id),
            started_at=session.started_at,
            ended_at=session.ended_at,
            has_pause_info=analysis.has_pause_info,
            pauses=[
                PauseResponse(started_at=start, ended_at=end)
                for start, end in session.pauses or ()
            ],
            observed_through=view.observed_through,
            basis=basis(view.definition_version, view.timezone),
            target_transitions=target_transitions_response(
                analysis.target_transitions, names
            ),
            composition=composition_response(
                analysis.composition, analysis_us=analysis.analysis_us
            ),
            distractions=[
                distraction_response(row, names) for row in analysis.distractions
            ],
            lapses=[lapse_response(row, names) for row in analysis.lapses],
            label_transitions=label_transitions_response(
                analysis.label_transitions,
                pending_us=analysis.composition.pending_us,
            ),
        )


def basis(definition_version: int, timezone: Timezone) -> AnalysisBasisResponse:
    return AnalysisBasisResponse(
        status="current", definition_version=definition_version, timezone=timezone
    )


def target_response(
    target: UsageTarget, names: Mapping[str, str]
) -> UsageTargetResponse:
    if target.kind is UsageTargetKind.APP:
        display_name = names.get(target.key, target.key)
        return UsageTargetResponse(
            kind="app", key=target.key, display_name=display_name
        )
    return UsageTargetResponse(
        kind="site", key=target.key, display_name=target.display_name()
    )


def lapse_target_response(
    target: LapseTarget | None, names: Mapping[str, str]
) -> UsageTargetResponse | None:
    """`앱 미확인` has no usage target and is null."""
    if not isinstance(target, UsageTarget):
        return None
    return target_response(target, names)


def target_transitions_response(
    transitions: Sequence[TargetTransition], names: Mapping[str, str]
) -> TargetTransitionsResponse:
    return TargetTransitionsResponse(
        count=len(transitions),
        short_dwell_count=short_dwell_count(transitions),
        items=[
            TargetTransitionResponse(
                at=row.at,
                previous=target_response(row.previous, names),
                next=target_response(row.next, names),
                previous_dwell_us=row.previous_dwell_us,
            )
            for row in transitions
        ],
    )


def label_transitions_response(
    transitions: Sequence[LabelTransition], *, pending_us: int
) -> LabelTransitionsResponse:
    return LabelTransitionsResponse(
        count=len(transitions),
        pending_us=pending_us,
        items=[
            LabelTransitionResponse(
                at=row.at,
                previous_label_id=UUID(row.previous),
                next_label_id=UUID(row.next),
            )
            for row in transitions
        ],
    )


def composition_response(
    composition: TimeComposition, *, analysis_us: int
) -> CompositionResponse:
    return CompositionResponse(
        analysis_us=analysis_us,
        focus_us=composition.focus_us,
        lapse_us=composition.lapse_us,
        unclassified_us=composition.unclassified_us,
        pending_us=composition.pending_us,
        opaque_us=composition.opaque_us,
        unobserved_us=composition.unobserved_us,
        lapse_count=composition.lapse_count,
    )


def distraction_response(
    row: Distraction, names: Mapping[str, str]
) -> DistractionResponse:
    return DistractionResponse(
        target=lapse_target_response(row.target, names),
        entries=row.entries,
        lapse_us=row.lapse_us,
    )


def lapse_response(row: LapseEvidence, names: Mapping[str, str]) -> LapseResponse:
    # A lapse lasts at most until the end of a non-empty analysis time.
    assert row.end is not None
    return LapseResponse(
        started_at=row.start,
        ended_at=row.end,
        entry=LapseEntryResponse(
            target=lapse_target_response(row.entry_target, names),
            label_id=None if row.entry_label_id is None else UUID(row.entry_label_id),
            context=DetailedActivityContext.model_validate(row.entry_context),
        ),
        previous_focus_target=lapse_target_response(row.previous_focus_target, names),
        elapsed_us=row.elapsed_us,
        lapse_us=row.lapse_us,
        transition_count=row.transitions,
        returned=row.returned,
    )
