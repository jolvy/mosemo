"""일간 통계 C1·C2·C7과 날짜 읽기(D1·D2)."""

from collections import Counter
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

from .session import composition, distractions, measure
from .sites import PublicSuffixList
from .timeline import (
    Segment,
    Timeline,
    adjacent,
    label_transitions,
    micros,
    parse,
    target_transitions,
)

SHORT_DWELL_US = 5_000_000
COMPOSITION_KEYS = (
    "focus_us",
    "lapse_us",
    "unclassified_us",
    "pending_us",
    "opaque_us",
    "unobserved_us",
    "lapse_count",
)


def day_window(timezone: str, day: str) -> tuple[datetime, datetime]:
    """계정 시간대 날짜의 `[시작, 끝)`(UTC). 일광 절약 시간 전환일은 23·25시간이다."""
    zone = ZoneInfo(timezone)
    first = date.fromisoformat(day)
    start = datetime.combine(first, time(), tzinfo=zone)
    end = datetime.combine(first + timedelta(days=1), time(), tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def read_day(data: Mapping[str, Any], day: str, psl: str) -> list[str]:
    """일간 통계 계산에 읽는 구간 id(구간 순서).

    날짜 창과 그날 시작한 완료 세션의 창(세션 시작~종료)마다
    D1: `started_at < 끝 AND COALESCE(ended_at, last_observed_at) >= 시작`,
    D2: 첫 구간에서 같은 사용 대상으로 맞닿은 앞 구간을 창 밖까지 거슬러 읽는다.
    관찰 침묵 횟수를 위해 효과 끝이 날짜 안에 있는 닫힌 활동 구간의 다음 구간 하나를 더 읽는다.
    `psl`은 사용 대상(사이트) 비교에 쓰며 의미는 `daily`와 같다(#268 사후 정정).
    """
    timeline = parse(data)
    sites = PublicSuffixList(psl)
    segments = timeline.segments
    start, end = day_window(timeline.timezone, day)
    windows = [(start, end)] + [
        (focus.started_at, focus.ended_at)
        for focus in timeline.sessions.values()
        if start <= focus.started_at < end
    ]
    chosen = {
        index for window in windows for index in read_window(segments, sites, window)
    }
    chosen |= {
        index + 1
        for index in chosen
        if index + 1 < len(segments) and silence_end(segments[index], (start, end))
    }
    return [segments[index].id for index in sorted(chosen)]


def read_window(
    segments: list[Segment], sites: PublicSuffixList, window: tuple[datetime, datetime]
) -> list[int]:
    """창 하나의 D1 행에 D2로 거슬러 읽은 행을 더한 구간 위치."""
    start, end = window
    chosen = [
        index
        for index, segment in enumerate(segments)
        if segment.started_at < end
        and (segment.observed_end is None or segment.observed_end >= start)
    ]
    # 끝이 없는 공백은 날짜보다 앞에서 시작해도 골라지므로 거슬러 읽기의 시작점이 아니다.
    closed = [index for index in chosen if segments[index].observed_end is not None]
    if closed:
        index = closed[0]
        target = segments[index].target(sites)
        while (
            target is not None
            and index > 0
            and adjacent(segments[index - 1], segments[index])
            and segments[index - 1].target(sites) == target
        ):
            index -= 1
            chosen.append(index)
    return chosen


def silence_end(segment: Segment, window: tuple[datetime, datetime]) -> bool:
    """닫힌 활동 구간이고 효과 끝이 날짜 창 `[시작, 끝)` 안에 있다(관찰 침묵의 앞 구간 후보)."""
    return (
        segment.is_activity
        and segment.ended_at is not None
        and window[0] <= segment.ended_at < window[1]
    )


def daily(data: Mapping[str, Any], day: str, psl: str) -> dict[str, Any]:
    """주어진 구간 전체로 계산한 그 날짜의 C1·C2·C7, 세션 합, 보조 값."""
    timeline = parse(data)
    sites = PublicSuffixList(psl)
    start, end = day_window(timeline.timezone, day)
    c2 = [
        transition.row()
        for transition in target_transitions(timeline.segments, sites)
        if start <= transition.at < end
    ]
    return {
        "c1": usage(timeline.segments, sites, (start, end)),
        "c2": c2,
        "c7": [
            transition.row()
            for transition in label_transitions(timeline, timeline.segments)
            if start <= transition.at < end
        ],
        "sessions": session_totals(timeline, sites, (start, end)),
        "aux": auxiliary(timeline, sites, (start, end), c2),
    }


def session_totals(
    timeline: Timeline, sites: PublicSuffixList, window: tuple[datetime, datetime]
) -> dict[str, Any]:
    """그날 시작한 완료 세션들의 분석 시간·C4·C5 합. 자정을 넘는 세션은 시작한 날에만 더한다."""
    count = analysis = 0
    composition_totals: Counter[str] = Counter(dict.fromkeys(COMPOSITION_KEYS, 0))
    rows: dict[str, list[int]] = {}
    for focus in timeline.sessions.values():
        if not window[0] <= focus.started_at < window[1]:
            continue
        measured = measure(timeline, focus)
        count += 1
        analysis += micros(measured.analysis)
        composition_totals.update(
            composition(measured.pieces, measured.analysis, len(measured.lapses))
        )
        for row in distractions(measured.pieces, sites):
            totals = rows.setdefault(row["key"], [0, 0])
            totals[0] += row["entries"]
            totals[1] += row["lapse_us"]
    return {
        "count": count,
        "analysis_us": analysis,
        "composition": {key: composition_totals[key] for key in COMPOSITION_KEYS},
        "distractions": [
            {"key": key, "entries": entries, "lapse_us": us}
            for key, (entries, us) in sorted(
                rows.items(), key=lambda item: (-item[1][1], item[0])
            )
        ],
    }


def auxiliary(
    timeline: Timeline,
    sites: PublicSuffixList,
    window: tuple[datetime, datetime],
    c2: list[dict[str, Any]],
) -> dict[str, int]:
    """보조 값 다섯 개(지표 명세 「결정 (2026-11-15)」)."""
    start, end = window
    segments = timeline.segments
    return {
        "short_dwell_transition_count": sum(
            1 for row in c2 if row["dwell_us"] < SHORT_DWELL_US
        ),
        "browser_without_url_us": sum(
            overlap_us(segment, window)
            for segment in segments
            if segment.is_activity and segment.is_browser_app
        ),
        "capture_gap_count": sum(
            1
            for segment in segments
            if not segment.is_activity
            and segment.started_at < end
            and (segment.ended_at is None or segment.ended_at >= start)
        ),
        "silence_count": sum(
            1
            for before, after in pairwise(segments)
            if silence_end(before, window) and not adjacent(before, after)
        ),
        "pending_us": sum(
            overlap_us(segment, window)
            for segment in segments
            if segment.is_activity
            and not segment.is_opaque
            and timeline.confirmation(segment) is None
        ),
    }


def overlap_us(segment: Segment, window: tuple[datetime, datetime]) -> int:
    """활동 구간이 창과 겹친 시간. 열린 구간은 last_observed_at까지다."""
    if segment.observed_end is None:
        return 0
    overlap = min(segment.observed_end, window[1]) - max(segment.started_at, window[0])
    return max(micros(overlap), 0)


def usage(
    segments: list[Segment], sites: PublicSuffixList, window: tuple[datetime, datetime]
) -> dict[str, Any]:
    """C1. 열린 구간은 last_observed_at까지, 날짜와 겹친 시간만 합한다."""
    apps: Counter[str] = Counter()
    targets: Counter[str] = Counter()
    unknown = opaque = observed = 0
    for segment in segments:
        if not segment.is_activity or segment.observed_end is None:
            continue
        overlap = min(segment.observed_end, window[1]) - max(
            segment.started_at, window[0]
        )
        us = max(micros(overlap), 0)
        observed += us
        if segment.is_opaque:
            opaque += us
        elif segment.app is None:
            unknown += us
        else:
            apps[segment.app] += us
            targets[segment.key(sites)] += us
    return {
        "apps": ranking(apps),
        "targets": ranking(targets),
        "unknown_app_us": unknown,
        "opaque_us": opaque,
        "observed_us": observed,
    }


def ranking(totals: Counter[str]) -> list[dict[str, Any]]:
    """FR-4 순서: 시간 내림차순, 같으면 key 오름차순. 0인 항목은 뺀다."""
    return [
        {"key": key, "us": us}
        for key, us in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
        if us > 0
    ]
