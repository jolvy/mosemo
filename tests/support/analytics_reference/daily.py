"""일간 통계 C1·C2·C7과 날짜 읽기(D1·D2)."""

from collections import Counter
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .sites import PublicSuffixList
from .timeline import (
    Segment,
    adjacent,
    label_transitions,
    micros,
    parse,
    target_transitions,
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

    D1: `started_at < 끝 AND COALESCE(ended_at, last_observed_at) >= 시작`.
    D2: 첫 구간에서 같은 사용 대상으로 맞닿은 앞 구간을 날짜 밖까지 거슬러 읽는다.
    `psl`은 사용 대상(사이트) 비교에 쓰며 의미는 `daily`와 같다(#268 사후 정정).
    """
    timeline = parse(data)
    sites = PublicSuffixList(psl)
    segments = timeline.segments
    start, end = day_window(timeline.timezone, day)
    chosen = [
        index
        for index, segment in enumerate(segments)
        if segment.started_at < end
        and (segment.observed_end is None or segment.observed_end >= start)
    ]
    if chosen:
        index = chosen[0]
        target = segments[index].target(sites)
        while (
            target is not None
            and index > 0
            and adjacent(segments[index - 1], segments[index])
            and segments[index - 1].target(sites) == target
        ):
            index -= 1
            chosen.append(index)
    return [segments[index].id for index in sorted(set(chosen))]


def daily(data: Mapping[str, Any], day: str, psl: str) -> dict[str, Any]:
    """주어진 구간 전체로 계산한 그 날짜의 C1·C2·C7."""
    timeline = parse(data)
    sites = PublicSuffixList(psl)
    start, end = day_window(timeline.timezone, day)
    return {
        "c1": usage(timeline.segments, sites, (start, end)),
        "c2": [
            transition.row()
            for transition in target_transitions(timeline.segments, sites)
            if start <= transition.at < end
        ],
        "c7": [
            transition.row()
            for transition in label_transitions(timeline, timeline.segments)
            if start <= transition.at < end
        ],
    }


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
