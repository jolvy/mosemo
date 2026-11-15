"""생성 전략이 경계 분포(`gen.py`)와 DF-5가 요구한 세션 입력을 실제로 만든다."""

from collections.abc import Callable
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

from hypothesis import Phase, given, settings

from tests.support import analytics_reference
from tests.support.analytics_reference.daily import day_window
from tests.support.analytics_strategies import (
    OPAQUE_SECRET,
    PSL,
    GeneratedTimeline,
    timelines,
)

Feature = Callable[[GeneratedTimeline], bool]


def moments(data: dict[str, Any], key: str) -> set[datetime]:
    return {
        datetime.fromisoformat(item[key])
        for item in data["segments"]
        if item.get(key) is not None
    }


def midnights(timeline: GeneratedTimeline) -> set[datetime]:
    return {day_window(timeline.data["timezone"], day)[0] for day in timeline.days}


def activities(timeline: GeneratedTimeline) -> list[dict[str, Any]]:
    return [s for s in timeline.data["segments"] if s["segment_type"] == "activity"]


def zero_length(timeline: GeneratedTimeline) -> bool:
    return any(s["ended_at"] == s["started_at"] for s in activities(timeline))


def ends_at_midnight(timeline: GeneratedTimeline) -> bool:
    return bool(moments(timeline.data, "ended_at") & midnights(timeline))


def silence(timeline: GeneratedTimeline) -> bool:
    segments = timeline.data["segments"]
    return any(
        before["ended_at"] is not None and before["ended_at"] != after["started_at"]
        for before, after in pairwise(segments)
    )


def capture_gap(timeline: GeneratedTimeline) -> bool:
    return any(s["segment_type"] == "capture_gap" for s in timeline.data["segments"])


def endless_gap(timeline: GeneratedTimeline) -> bool:
    return any(
        s["segment_type"] == "capture_gap" and s["ended_at"] is None
        for s in timeline.data["segments"]
    )


def opaque_secret(timeline: GeneratedTimeline) -> bool:
    return OPAQUE_SECRET in str(timeline.data["segments"])


def unknown_app(timeline: GeneratedTimeline) -> bool:
    return any(
        s["context"].get("app", {}).get("bundleId", {}).get("status")
        in {"absent", "unavailable"}
        for s in activities(timeline)
    )


def url_like(text: str) -> Feature:
    return lambda timeline: text in str(timeline.data["segments"])


def dst_day(timeline: GeneratedTimeline) -> bool:
    zone = ZoneInfo(timeline.data["timezone"])
    return any(
        (end - start) != timedelta(hours=24)
        for start, end in (day_window(zone.key, day) for day in timeline.days)
    )


def session_crosses_midnight(timeline: GeneratedTimeline) -> bool:
    zone = ZoneInfo(timeline.data["timezone"])
    return any(
        datetime.fromisoformat(item["started_at"]).astimezone(zone).date()
        != datetime.fromisoformat(item["ended_at"]).astimezone(zone).date()
        for item in timeline.data["sessions"]
    )


def session_ends_paused(timeline: GeneratedTimeline) -> bool:
    return any(
        item["pauses"] and item["pauses"][-1][1] == item["ended_at"]
        for item in timeline.data["sessions"]
    )


def lapse_keys(timeline: GeneratedTimeline) -> list[set[str]]:
    return [
        {
            row["key"]
            for row in analytics_reference.session(timeline.data, sid, PSL)["c5"]
        }
        for sid in timeline.session_ids
    ]


def same_distraction_in_two_sessions(timeline: GeneratedTimeline) -> bool:
    keys = lapse_keys(timeline)
    return len(keys) == 2 and bool(keys[0] & keys[1])


def unknown_app_lapse(timeline: GeneratedTimeline) -> bool:
    return any("앱 미확인" in keys for keys in lapse_keys(timeline))


FEATURES: dict[str, Feature] = {
    "0초 구간": zero_length,
    "자정에 끝나는 구간": ends_at_midnight,
    "침묵": silence,
    "수집 공백": capture_gap,
    "끝 없는 수집 공백": endless_gap,
    "숨긴 값이 든 불투명": opaque_secret,
    "앱 미확인": unknown_app,
    "IDN URL": url_like("예시.한국"),
    "IP URL": url_like("192.168.0.1"),
    "scheme 없는 URL": url_like("meet.google.com/x"),
    "일광 절약 시간 전환일": dst_day,
    "자정을 넘는 세션": session_crosses_midnight,
    "일시중지로 끝나는 세션": session_ends_paused,
    "두 세션에 같은 방해 대상": same_distraction_in_two_sessions,
    "앱 미확인 이탈": unknown_app_lapse,
}


def test_strategy_generates_every_boundary_feature() -> None:
    seen: set[str] = set()

    @settings(max_examples=300, phases=[Phase.generate])
    @given(timelines())
    def collect(timeline: GeneratedTimeline) -> None:
        seen.update(name for name, feature in FEATURES.items() if feature(timeline))

    collect()

    assert sorted(set(FEATURES) - seen) == []


@settings(max_examples=20)
@given(timelines())
def test_strategy_gives_the_same_timeline_to_both_implementations(
    timeline: GeneratedTimeline,
) -> None:
    assert [segment.id for segment in timeline.snapshot.segments] == [
        item["id"] for item in timeline.data["segments"]
    ]
