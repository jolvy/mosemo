"""제품 분석 계산 모듈을 참조 구현과 같은 입출력 형태로 부르는 어댑터.

입력 data는 참조 구현(#268 「지켜야 할 계약」)의 JSON 형태이고, `snapshot`이 제품 입력
`SourceSnapshot`으로 바꾼다. `daily`·`session`·`read_day`는 참조 구현의 같은 이름 함수와 같은
인자를 받아 같은 형태의 dict·목록을 돌려주므로, 사례 표·차등·변형 관계 검사를 두 구현에 그대로 쓴다.
"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from functools import cache
from typing import Any

from mosemo.analytics.domain.daily_stats import DailyStats, DailyStatsCalculator
from mosemo.analytics.domain.day_reading import select_day_segments
from mosemo.analytics.domain.session_analysis import (
    Distraction,
    SessionAnalysis,
    SessionAnalyzer,
    TimeComposition,
)
from mosemo.analytics.domain.source import (
    CompletedFocusSession,
    ConfirmedLabel,
    DayWindow,
    SegmentKind,
    SourceSegment,
    SourceSnapshot,
)
from mosemo.analytics.domain.transitions import LabelTransition, TargetTransition
from mosemo.analytics.domain.usage_targets import UsageTargetResolver
from mosemo.shared.domain.timezones import Timezone


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def optional_time(value: str | None) -> datetime | None:
    return None if value is None else parse_time(value)


def format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def segment_of(item: Mapping[str, Any]) -> SourceSegment:
    is_activity = item["segment_type"] == "activity"
    ended_at = optional_time(item.get("ended_at"))
    observed_end = ended_at
    if is_activity and ended_at is None:
        observed_end = parse_time(item["last_observed_at"])
    return SourceSegment(
        id=item["id"],
        first_event_id=item["first_event_id"],
        kind=SegmentKind.ACTIVITY if is_activity else SegmentKind.CAPTURE_GAP,
        started_at=parse_time(item["started_at"]),
        ended_at=ended_at,
        observed_end=observed_end,
        version=item.get("version") if is_activity else None,
        context=item.get("context") if is_activity else None,
    )


def session_of(item: Mapping[str, Any]) -> CompletedFocusSession:
    pauses = item.get("pauses")
    return CompletedFocusSession(
        id=item["id"],
        started_at=parse_time(item["started_at"]),
        ended_at=parse_time(item["ended_at"]),
        label_id=item["label_id"],
        pauses=None
        if pauses is None
        else tuple((parse_time(low), parse_time(high)) for low, high in pauses),
    )


def snapshot(data: Mapping[str, Any]) -> SourceSnapshot:
    """참조 구현 입력 → 제품 입력. 진행 중 세션(`ended_at` 없음)은 넣지 않는다."""
    return SourceSnapshot(
        timezone=Timezone(data["timezone"]),
        segments=tuple(segment_of(item) for item in data.get("segments", [])),
        confirmations={
            item["first_event_id"]: ConfirmedLabel(
                segment_version=item["segment_version"], label_id=item["label_id"]
            )
            for item in data.get("confirmations", [])
        },
        sessions=tuple(
            session_of(item)
            for item in data.get("sessions", [])
            if item.get("ended_at") is not None
        ),
    )


@cache
def resolver(psl: str) -> UsageTargetResolver:
    return UsageTargetResolver(psl)


def window(data: Mapping[str, Any], day: str) -> DayWindow:
    return DayWindow.of(Timezone(data["timezone"]), date.fromisoformat(day))


def read_day(data: Mapping[str, Any], day: str, psl: str) -> list[str]:
    source = snapshot(data)
    chosen = select_day_segments(
        source.segments,
        sessions=source.sessions,
        window=window(data, day),
        resolver=resolver(psl),
    )
    return [segment.id for segment in chosen]


def daily(data: Mapping[str, Any], day: str, psl: str) -> dict[str, Any]:
    stats = DailyStatsCalculator(resolver(psl)).compute(
        snapshot(data), window(data, day)
    )
    return daily_output(stats)


def session(data: Mapping[str, Any], session_id: str, psl: str) -> dict[str, Any]:
    source = snapshot(data)
    focus = next(item for item in source.sessions if item.id == session_id)
    return session_output(SessionAnalyzer(resolver(psl)).analyze(source, focus))


def target_row(row: TargetTransition) -> dict[str, Any]:
    return {
        "at": format_time(row.at),
        "prev": row.previous.key,
        "next": row.next.key,
        "dwell_us": row.previous_dwell_us,
    }


def label_row(row: LabelTransition) -> dict[str, Any]:
    return {"at": format_time(row.at), "prev": row.previous, "next": row.next}


def composition_output(composition: TimeComposition) -> dict[str, int]:
    return {item.name: getattr(composition, item.name) for item in fields(composition)}


def distraction_rows(rows: tuple[Distraction, ...]) -> list[dict[str, Any]]:
    return [
        {"key": row.target.key, "entries": row.entries, "lapse_us": row.lapse_us}
        for row in rows
    ]


def daily_output(stats: DailyStats) -> dict[str, Any]:
    usage = stats.usage
    return {
        "c1": {
            "apps": [{"key": row.app, "us": row.us} for row in usage.apps],
            "targets": [{"key": row.target.key, "us": row.us} for row in usage.targets],
            "unknown_app_us": usage.unknown_app_us,
            "opaque_us": usage.opaque_us,
            "observed_us": usage.observed_us,
        },
        "c2": [target_row(row) for row in stats.target_transitions],
        "c7": [label_row(row) for row in stats.label_transitions],
        "sessions": {
            "count": stats.sessions.count,
            "analysis_us": stats.sessions.analysis_us,
            "composition": composition_output(stats.sessions.composition),
            "distractions": distraction_rows(stats.sessions.distractions),
        },
        "aux": {
            item.name: getattr(stats.auxiliary, item.name)
            for item in fields(stats.auxiliary)
        },
    }


def session_output(analysis: SessionAnalysis) -> dict[str, Any]:
    return {
        "has_pause_info": analysis.has_pause_info,
        "analysis_us": analysis.analysis_us,
        "c3": [target_row(row) for row in analysis.target_transitions],
        "c4": composition_output(analysis.composition),
        "c5": distraction_rows(analysis.distractions),
        "c6": [
            {
                "start": format_time(row.start),
                "end": None if row.end is None else format_time(row.end),
                "entry_segment_id": row.entry_segment_id,
                "entry_target": row.entry_target.key,
                "entry_label_id": row.entry_label_id,
                "entry_context": row.entry_context,
                "prev_focus_target": None
                if row.previous_focus_target is None
                else row.previous_focus_target.key,
                "elapsed_us": row.elapsed_us,
                "lapse_us": row.lapse_us,
                "transitions": row.transitions,
                "returned": row.returned,
            }
            for row in analysis.lapses
        ],
        "c7": [label_row(row) for row in analysis.label_transitions],
    }


def flatten(value: object) -> list[object]:
    """출력 값 전체(dataclass·dict·목록 안의 모든 원시값). E8 검사에 쓴다."""
    if is_dataclass(value) and not isinstance(value, type):
        return [
            leaf
            for item in fields(value)
            for leaf in flatten(getattr(value, item.name))
        ]
    if isinstance(value, Mapping):
        return [leaf for key, item in value.items() for leaf in [key, *flatten(item)]]
    if isinstance(value, list | tuple):
        return [leaf for item in value for leaf in flatten(item)]
    return [value]
