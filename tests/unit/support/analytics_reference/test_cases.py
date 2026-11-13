"""지표 명세의 경계 사례·반례(`C\\d-[BX]\\d+`) 66개를 데이터로 고정한다.

`CASES`의 값은 `{"data", "call", "args", "expect"}`이고 `expect`의 키는 출력 경로다
(`tests.support.analytics_timelines.resolve`: 점으로 나누고 숫자는 목록 위치, `#`은 길이,
`*`는 목록 전체). 시각은 2026-11-03 00:00 Asia/Seoul부터의 분이다.
"""

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.support.analytics_reference import daily, read_day, session
from tests.support.analytics_timelines import (
    CHROME,
    CODING,
    DAY,
    LEISURE,
    MIN,
    OPAQUE,
    PSL,
    SAFARI,
    SESSION,
    activity,
    app,
    at,
    browser,
    focus,
    gap,
    only,
    resolve,
    timeline,
    unknown_app,
    utc,
)

SPEC = Path(__file__).resolve().parents[4] / "docs/activity-analytics-metrics.md"
YT = "https://m.youtube.com/watch?v=1"
YT2 = "https://www.youtube.com/watch?v=2"
GH = "https://github.com/jolvy"
SLACK = "com.tinyspeck.slackmacgap"


def on_day(data: dict[str, Any], expect: dict[str, Any], day: str = DAY) -> dict:
    return {"data": data, "call": "daily", "args": [day, PSL], "expect": expect}


def in_session(data: dict[str, Any], expect: dict[str, Any]) -> dict:
    return {"data": data, "call": "session", "args": [SESSION, PSL], "expect": expect}


def c4(**values: int) -> dict[str, int]:
    """C4 전체 값. 시간은 분, `lapse_count`는 건수다."""
    names = ("focus", "lapse", "unclassified", "pending", "opaque", "unobserved")
    return {f"{name}_us": values.get(name, 0) * MIN for name in names} | {
        "lapse_count": values.get("lapse_count", 0)
    }


C2_B1 = timeline(
    activity("s1", 0, 5, browser(YT)),
    activity("s2", 5, 6, browser(YT2)),
    activity("s3", 6, 10, browser(GH)),
)
C2_B1_ROW = {
    "at": utc(6),
    "prev": "youtube.com",
    "next": "github.com",
    "dwell_us": 6 * MIN,
}
C3_B1 = timeline(
    activity("s1", -10, 0, app("A")),
    activity("s2", 0, 60, app("B")),
    sessions=[focus()],
)
C5_B1 = timeline(
    activity("s1", 0, 10, app("A")),
    activity("s2", 10, 15, browser(YT)),
    activity("s3", 15, 20, app(SLACK)),
    activity("s4", 20, 25, browser(YT2)),
    activity("s5", 25, 60, app("A")),
    labels={"s1": CODING, "s2": LEISURE, "s3": LEISURE, "s4": LEISURE, "s5": CODING},
    sessions=[focus()],
)
C7_B1 = timeline(
    activity("s1", 0, 10, app("A")),
    activity("s2", 10, 10, app("A")),
    activity("s3", 10, 20, app("A")),
    labels={"s1": CODING, "s2": LEISURE, "s3": CODING},
)
C7_B2 = timeline(
    activity("s1", 0, 10, app("A")),
    activity("s2", 10, 20, app("B")),
    activity("s3", 20, 30, app("C")),
    labels={"s1": CODING, "s3": LEISURE},
)

CASES: dict[str, dict] = {
    # C1 앱·사이트 사용 시간
    "C1-B1": on_day(
        timeline(activity("s1", -30, 30, app("A"))),
        {"c1.apps": [{"key": "A", "us": 30 * MIN}], "c1.observed_us": 30 * MIN},
    ),
    "C1-B2": on_day(
        timeline(activity("s1", 0, None, app("A"), last=10)),
        {"c1.apps": [{"key": "A", "us": 10 * MIN}], "c1.observed_us": 10 * MIN},
    ),
    "C1-B3": on_day(
        timeline(activity("s1", 0, 10, app("A")), activity("s2", 20, 30, app("B"))),
        {"c1.observed_us": 20 * MIN},
    ),
    "C1-B4": on_day(
        timeline(
            activity("s1", 0, 10, app("A")),
            gap("g1", 10, 40),
            activity("s2", 40, 50, app("A")),
        ),
        {"c1.apps": [{"key": "A", "us": 20 * MIN}], "c1.observed_us": 20 * MIN},
    ),
    "C1-B5": on_day(
        timeline(activity("s1", 0, 10, app("A")), activity("s2", 10, 10, app("B"))),
        {"c1.apps": [{"key": "A", "us": 10 * MIN}]},
    ),
    "C1-B6": on_day(
        timeline(
            activity("s1", 0, 20, browser(YT)),
            activity("s2", 20, 25, browser(None)),
            activity("s3", 25, 55, app("A")),
        ),
        {
            "c1.apps": [{"key": "A", "us": 30 * MIN}, {"key": CHROME, "us": 25 * MIN}],
            "c1.targets": [
                {"key": "A", "us": 30 * MIN},
                {"key": "youtube.com", "us": 20 * MIN},
                {"key": CHROME, "us": 5 * MIN},
            ],
        },
    ),
    "C1-B7": on_day(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 10, 12, unknown_app()),
            activity("s3", 12, 17, OPAQUE),
        ),
        {
            "c1.unknown_app_us": 2 * MIN,
            "c1.opaque_us": 5 * MIN,
            "c1.observed_us": 17 * MIN,
        },
    ),
    "C1-B8": on_day(
        timeline(
            activity(
                "s1", "2026-03-07T23:00:00-05:00", "2026-03-09T01:00:00-04:00", app("A")
            ),
            timezone="America/New_York",
        ),
        {"c1.observed_us": 1380 * MIN},
        day="2026-03-08",
    ),
    "C1-B9": on_day(
        timeline(
            activity("s1", 0, 1, browser("http://192.168.0.1:8080/")),
            activity("s2", 1, 3, browser("http://localhost:8000/docs")),
            activity("s3", 3, 6, browser("http://intranet/wiki")),
            activity("s4", 6, 10, browser("https://github.io/")),
        ),
        {
            "c1.targets": [
                {"key": "github.io", "us": 4 * MIN},
                {"key": "intranet", "us": 3 * MIN},
                {"key": "localhost", "us": 2 * MIN},
                {"key": "192.168.0.1", "us": 1 * MIN},
            ]
        },
    ),
    "C1-B10": on_day(
        timeline(
            activity("s1", 0, 20, browser(YT)),
            activity("s2", 20, 22, browser("chrome://settings")),
            activity("s3", 22, 24, browser("about:blank")),
            activity("s4", 24, 25, browser("file:///Users/a.pdf")),
        ),
        {
            "c1.targets": [
                {"key": "youtube.com", "us": 20 * MIN},
                {"key": CHROME, "us": 5 * MIN},
            ]
        },
    ),
    "C1-X1": on_day(
        timeline(activity("s1", 0, None, app("A"), last=10)),
        {"c1.observed_us": 10 * MIN},
    ),
    "C1-X2": on_day(
        timeline(
            activity("s1", 0, 20, browser(YT)), activity("s2", 20, 30, browser(GH))
        ),
        {
            "c1.apps": [{"key": CHROME, "us": 30 * MIN}],
            "c1.targets": [
                {"key": "youtube.com", "us": 20 * MIN},
                {"key": "github.com", "us": 10 * MIN},
            ],
        },
    ),
    "C1-X3": on_day(
        timeline(activity("s1", 30, 40, app("A"))),
        {"c1.observed_us": 10 * MIN},
    ),
    # C2 날짜별 사용 대상 전환
    "C2-B1": on_day(C2_B1, {"c2": [C2_B1_ROW]}),
    "C2-B2": on_day(
        timeline(
            activity("s1", 0, 5, browser(YT)),
            activity("s2", 5, 10, browser(YT, bundle_id=SAFARI)),
        ),
        {"c2.#": 0},
    ),
    "C2-B3": on_day(
        timeline(
            activity("s1", 0, 5, app("A")),
            gap("g1", 5, 10),
            activity("s2", 10, 15, app("B")),
        ),
        {"c2.#": 0},
    ),
    "C2-B4": on_day(
        timeline(activity("s1", 0, 5, app("A")), activity("s2", 6, 10, app("B"))),
        {"c2.#": 0},
    ),
    "C2-B5": on_day(
        timeline(
            activity("s1", 0, 5, app("A")),
            activity("s2", 5, 10, OPAQUE),
            activity("s3", 10, 15, app("B")),
            activity("s4", 15, 20, unknown_app()),
            activity("s5", 20, 25, app("A")),
        ),
        {"c2.#": 0},
    ),
    "C2-B6": on_day(
        timeline(
            activity("s1", 0, 5, app("A")),
            activity("s2", 5, 5, app("B")),
            activity("s3", 5, 10, app("C")),
        ),
        {"c2.#": 2, "c2.*.next": ["B", "C"], "c2.*.dwell_us": [5 * MIN, 0]},
    ),
    "C2-B7": on_day(
        timeline(
            activity("s1", 0, 5, app("A")),
            activity("s2", 5, 10, app("B")),
            activity("s3", 10, 15, app("A")),
        ),
        {"c2.#": 2},
    ),
    "C2-B8": on_day(
        timeline(
            activity("s1", -15, 0, browser(YT)),
            activity("s2", 0, 0, app("A")),
            activity("s3", 0, 10, app("B")),
        ),
        {"c2.#": 2, "c2.0.dwell_us": 15 * MIN},
    ),
    "C2-B9": on_day(
        timeline(activity("s1", 0, None, app("A"), last=10)),
        {"c2.#": 0},
    ),
    "C2-B10": on_day(
        timeline(
            activity("s1", -60, -30, app("A", "1")),
            activity("s2", -30, 20, app("A", "2")),
            activity("s3", 20, 30, app("C")),
            gap("g1", 30, 600),
            activity("s4", 600, 610, app("B")),
            activity("s5", 610, 620, app("A", "1")),
            activity("s6", 620, 650, app("A", "2")),
            activity("s7", 650, 660, app("C")),
            gap("g2", 660, 700),
            activity("s8", 700, 710, app("A", "1")),
            activity("s9", 720, 730, app("A", "2")),
            activity("s10", 730, 750, app("A", "3")),
            activity("s11", 750, 760, app("C")),
        ),
        {
            "c2.*.prev": ["A", "B", "A", "A"],
            "c2.*.dwell_us": [80 * MIN, 10 * MIN, 40 * MIN, 30 * MIN],
        },
    ),
    "C2-X1": on_day(C2_B1, {"c2.0.dwell_us": 6 * MIN}),
    "C2-X2": on_day(
        timeline(activity("s1", 0, 5, browser(YT)), activity("s2", 5, 10, browser(GH))),
        {"c2.#": 1},
    ),
    "C2-X3": on_day(
        timeline(
            activity("s1", 0, 5, app("A")),
            activity("s2", 5, 10, OPAQUE),
            activity("s3", 10, 15, app("B")),
        ),
        {"c2.#": 0},
    ),
    "C2-X4": on_day(
        timeline(
            activity("s1", 0, 5, app("A")),
            activity("s2", 5, at(5, 1), app("B")),
            activity("s3", at(5, 1), 10, app("A")),
        ),
        {"c2.#": 2, "c2.1.dwell_us": 1_000_000},
    ),
    # C3 세션별 사용 대상 전환
    "C3-B1": in_session(C3_B1, {"c3.#": 0}),
    "C3-B2": in_session(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 10, 15, app("B")),
            activity("s3", 15, 20, app("C")),
            activity("s4", 20, 30, app("D")),
            activity("s5", 30, 60, app("E")),
            sessions=[focus(pauses=[(10, 20)])],
        ),
        {"c3.*.at": [utc(30)]},
    ),
    "C3-B3": in_session(
        timeline(
            activity("s1", 0, 60, app("A")),
            activity("s2", 60, 70, app("B")),
            sessions=[focus()],
        ),
        {"c3.#": 0},
    ),
    "C3-B4": in_session(
        timeline(
            activity("s1", -10, 5, app("A")),
            activity("s2", 5, 60, app("B")),
            sessions=[focus()],
        ),
        {"c3": [{"at": utc(5), "prev": "A", "next": "B", "dwell_us": 15 * MIN}]},
    ),
    "C3-X1": in_session(C3_B1, {"c3.#": 0}),
    "C3-X2": in_session(
        timeline(
            activity("s1", 0, 12, app("A")),
            activity("s2", 12, 60, app("B")),
            sessions=[focus(pauses=[(10, 20)])],
        ),
        {"c3.#": 0},
    ),
    # C4 세션 시간 구성
    "C4-B1": in_session(
        timeline(
            activity("s1", -10, 10, app("A")),
            labels={"s1": CODING},
            sessions=[focus()],
        ),
        {"c4": c4(focus=10, unobserved=50)},
    ),
    "C4-B2": in_session(
        timeline(
            activity("s1", 0, 60, app("A"), version="v2"),
            stale={"s1": CODING},
            sessions=[focus()],
        ),
        {"c4": c4(pending=60)},
    ),
    "C4-B3": in_session(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 10, 20, app("B")),
            activity("s3", 20, 30, app("C")),
            activity("s4", 30, 40, app("D")),
            activity("s5", 40, 50, OPAQUE),
            labels={"s1": CODING, "s2": LEISURE, "s3": None},
            sessions=[focus()],
        ),
        {
            "c4": c4(
                focus=10,
                lapse=10,
                unclassified=10,
                pending=10,
                opaque=10,
                unobserved=10,
                lapse_count=1,
            )
        },
    ),
    "C4-B4": in_session(
        timeline(
            activity("s1", 0, 60, app("A")),
            labels={"s1": CODING},
            sessions=[focus(pauses=None)],
        ),
        {"has_pause_info": False, "analysis_us": 60 * MIN, "c4": c4(focus=60)},
    ),
    "C4-B5": in_session(
        timeline(
            activity("s1", 0, 60, app("A")),
            labels={"s1": CODING},
            sessions=[focus(pauses=[(10, 30)])],
        ),
        {"has_pause_info": True, "analysis_us": 40 * MIN, "c4": c4(focus=40)},
    ),
    "C4-B6": in_session(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 10, 10, app("B")),
            activity("s3", 10, 60, app("A")),
            labels={"s1": CODING, "s2": LEISURE, "s3": CODING},
            sessions=[focus()],
        ),
        {"c4.lapse_count": 1, "c4.lapse_us": 0},
    ),
    "C4-X1": in_session(
        timeline(
            activity("s1", 0, 10, app("A")),
            labels={"s1": None},
            sessions=[focus()],
        ),
        {"c4": c4(unclassified=10, unobserved=50)},
    ),
    "C4-X2": in_session(
        timeline(sessions=[focus(pauses=None)]),
        {"analysis_us": 60 * MIN, "c4": c4(unobserved=60)},
    ),
    # C5 방해 앱
    "C5-B1": in_session(
        C5_B1,
        {
            "c5": [
                {"key": "youtube.com", "entries": 2, "lapse_us": 10 * MIN},
                {"key": SLACK, "entries": 1, "lapse_us": 5 * MIN},
            ]
        },
    ),
    "C5-B2": in_session(
        timeline(
            activity("s1", 10, 15, browser(YT)),
            activity("s2", 15, 20, browser(YT)),
            activity("s3", 20, 25, browser(YT2)),
            labels={"s1": LEISURE, "s3": LEISURE},
            sessions=[focus()],
        ),
        {"c5": [{"key": "youtube.com", "entries": 1, "lapse_us": 10 * MIN}]},
    ),
    "C5-B3": in_session(
        timeline(
            activity("s1", 10, 15, browser(YT)),
            activity("s2", 15, 20, OPAQUE),
            activity("s3", 20, 25, browser(YT2)),
            labels={"s1": LEISURE, "s3": LEISURE},
            sessions=[focus()],
        ),
        {"c5": [{"key": "youtube.com", "entries": 1, "lapse_us": 10 * MIN}]},
    ),
    "C5-B4": in_session(
        timeline(
            activity("s1", 10, 15, unknown_app()),
            activity("s2", 15, 20, app("B")),
            labels={"s1": LEISURE, "s2": LEISURE},
            sessions=[focus()],
        ),
        {
            "c5": [
                {"key": "B", "entries": 1, "lapse_us": 5 * MIN},
                {"key": "앱 미확인", "entries": 1, "lapse_us": 5 * MIN},
            ],
            "c4.lapse_us": 10 * MIN,
        },
    ),
    "C5-B5": in_session(
        timeline(
            activity("s1", 0, 5, app("B")),
            activity("s2", 5, 10, app("A")),
            activity("s3", 10, 15, app("B")),
            activity("s4", 15, 60, app("A")),
            labels={"s1": LEISURE, "s2": CODING, "s3": LEISURE, "s4": CODING},
            sessions=[focus()],
        ),
        {
            "c5": [{"key": "B", "entries": 2, "lapse_us": 10 * MIN}],
            "c4.lapse_count": 2,
        },
    ),
    "C5-X1": in_session(
        timeline(
            activity("s1", 10, 15, browser(YT)),
            activity("s2", 15, 20, app(SLACK)),
            labels={"s1": LEISURE, "s2": LEISURE},
            sessions=[focus()],
        ),
        {"c5.*.key": [SLACK, "youtube.com"], "c5.*.entries": [1, 1]},
    ),
    "C5-X2": in_session(
        timeline(
            activity("s1", 10, 15, browser(YT)),
            activity("s2", 15, 20, browser(YT2)),
            labels={"s1": LEISURE, "s2": LEISURE},
            sessions=[focus()],
        ),
        {"c5": [{"key": "youtube.com", "entries": 1, "lapse_us": 10 * MIN}]},
    ),
    # C6 이탈 근거
    "C6-B1": in_session(
        timeline(
            activity("s1", 0, 10, app("B")),
            activity("s2", 10, 60, app("A")),
            labels={"s1": LEISURE, "s2": CODING},
            sessions=[focus()],
        ),
        {
            "c6": [
                {
                    "start": utc(0),
                    "end": utc(10),
                    "entry_segment_id": "s1",
                    "entry_target": "B",
                    "entry_label_id": LEISURE,
                    "entry_context": app("B"),
                    "prev_focus_target": None,
                    "elapsed_us": 0,
                    "lapse_us": 10 * MIN,
                    "transitions": 0,
                    "returned": True,
                }
            ]
        },
    ),
    "C6-B2": in_session(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 10, 30, app("B")),
            labels={"s1": CODING, "s2": LEISURE},
            sessions=[focus()],
        ),
        {"c6.0.end": utc(60), "c6.0.returned": False, "c6.0.prev_focus_target": "A"},
    ),
    "C6-B3": in_session(C5_B1, {"c6.#": 1, "c6.0.transitions": 2}),
    "C6-B4": in_session(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 30, 40, app("B")),
            activity("s3", 40, 60, app("A")),
            labels={"s1": CODING, "s2": LEISURE, "s3": CODING},
            sessions=[focus(pauses=[(10, 25)])],
        ),
        {"c6.0.elapsed_us": 15 * MIN},
    ),
    "C6-B5": in_session(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 10, 15, app("B")),
            activity("s3", 15, 20, app("B")),
            activity("s4", 30, 35, app("B")),
            activity("s5", 35, 60, app("A")),
            labels={"s1": CODING, "s2": LEISURE, "s4": LEISURE, "s5": CODING},
            sessions=[focus(pauses=[(20, 30)])],
        ),
        {
            "c6.#": 1,
            "c6.0.start": utc(10),
            "c6.0.end": utc(35),
            "c6.0.lapse_us": 10 * MIN,
        },
    ),
    "C6-B6": in_session(
        timeline(
            activity("s1", 0, 20, app("A")),
            activity("s2", 20, 40, app("B")),
            labels={"s1": CODING, "s2": LEISURE},
            sessions=[focus(pauses=[(40, 60)])],
        ),
        {
            "c6.0.end": utc(40),
            "c6.0.lapse_us": 20 * MIN,
            "c6.0.transitions": 0,
            "c6.0.returned": False,
        },
    ),
    "C6-X1": in_session(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 10, 15, app("B")),
            activity("s3", 15, 35, app("B")),
            activity("s4", 35, 60, app("A")),
            labels={"s1": CODING, "s2": LEISURE, "s4": CODING},
            sessions=[focus()],
        ),
        {"c6.0.end": utc(35), "c6.0.lapse_us": 5 * MIN},
    ),
    "C6-X2": in_session(
        timeline(
            activity("s1", 0, 30, app("A")),
            activity("s2", 30, 45, app("B")),
            activity("s3", 45, 60, app("A")),
            labels={"s1": CODING, "s2": LEISURE, "s3": CODING},
            sessions=[focus(pauses=[(5, 20)])],
        ),
        {"c6.0.start": utc(30), "c6.0.elapsed_us": 15 * MIN},
    ),
    # C7 라벨 전환
    "C7-B1": on_day(C7_B1, {"c7.#": 2}),
    "C7-B2": on_day(C7_B2, {"c7.#": 0}),
    "C7-B3": on_day(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 10, 20, app("B")),
            activity("s3", 20, 30, app("C")),
            labels={"s1": CODING, "s2": None, "s3": LEISURE},
        ),
        {"c7.#": 0},
    ),
    "C7-B4": on_day(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 12, 20, app("B")),
            gap("g1", 20, 30),
            activity("s3", 30, 40, app("A")),
            activity("s4", 40, 50, OPAQUE),
            activity("s5", 50, 60, app("B")),
            labels={"s1": CODING, "s2": LEISURE, "s3": CODING, "s5": LEISURE},
        ),
        {"c7.#": 0},
    ),
    "C7-B5": on_day(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 10, 20, app("B"), version="v2"),
            labels={"s1": CODING},
            stale={"s2": LEISURE},
        ),
        {"c7.#": 0},
    ),
    "C7-B6": on_day(
        timeline(
            activity("s1", 0, 10, app("A")),
            activity("s2", 10, 20, app("B")),
            gap("g1", 20, 30),
            activity("s3", 30, 40, app("A")),
            activity("s4", 40, 50, app("A")),
            labels={"s1": CODING, "s2": CODING, "s3": CODING, "s4": LEISURE},
        ),
        {"c7": [{"at": utc(40), "prev": CODING, "next": LEISURE}]},
    ),
    "C7-B7": on_day(
        timeline(
            activity("s1", -15, 0, app("A")),
            activity("s2", 0, 0, app("B")),
            activity("s3", 0, 10, app("A")),
            labels={"s1": CODING, "s2": LEISURE, "s3": CODING},
        ),
        {"c7.#": 2},
    ),
    "C7-B8": in_session(
        timeline(
            activity("s1", -10, 0, app("B")),
            activity("s2", 0, 60, app("A")),
            labels={"s1": LEISURE, "s2": CODING},
            sessions=[focus()],
        ),
        {"c7.#": 0},
    ),
    "C7-X1": on_day(C7_B2, {"c7.#": 0}),
    "C7-X2": on_day(C7_B1, {"c7.#": 2}),
}

CALLS: dict[str, Any] = {"daily": daily, "session": session, "read_day": read_day}


def run(case: dict) -> Any:
    return CALLS[case["call"]](case["data"], *case["args"])


def spec_case_ids() -> set[str]:
    rows = SPEC.read_text(encoding="utf-8").splitlines()
    return {
        match.group(1)
        for row in rows
        if (match := re.match(r"^\|\s*(C\d-[BX]\d+)\s*\|", row))
    }


def spanned_days(data: dict[str, Any]) -> list[str]:
    zone = ZoneInfo(data["timezone"])
    moments = [
        datetime.fromisoformat(value).astimezone(zone).date()
        for item in data["segments"]
        for value in (
            item["started_at"],
            item["ended_at"],
            item.get("last_observed_at"),
        )
        if value is not None
    ]
    first, last = min(moments), max(moments)
    return [
        (first + timedelta(days=offset)).isoformat()
        for offset in range((last - first).days + 1)
    ]


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_case(case_id: str) -> None:
    case = CASES[case_id]
    output = run(case)

    for path, expected in case["expect"].items():
        assert resolve(output, path) == expected, path


@pytest.mark.parametrize(
    "case_id", sorted(key for key, case in CASES.items() if case["call"] == "daily")
)
def test_daily_case_holds_on_segments_read_for_the_day(case_id: str) -> None:
    case = CASES[case_id]
    day, psl = case["args"]
    chosen = only(case["data"], read_day(case["data"], day))

    output = daily(chosen, day, psl)

    for path, expected in case["expect"].items():
        assert resolve(output, path) == expected, path
    assert output == daily(case["data"], day, psl)


@pytest.mark.parametrize(
    "case_id", sorted(key for key, case in CASES.items() if case["call"] == "daily")
)
def test_read_day_matches_full_daily_on_every_spanned_day(case_id: str) -> None:
    data = CASES[case_id]["data"]

    for day in spanned_days(data):
        chosen = only(data, read_day(data, day))
        assert daily(chosen, day, PSL) == daily(data, day, PSL), day


def test_cases_cover_every_spec_case_id() -> None:
    assert len(spec_case_ids()) == 66
    assert set(CASES) == spec_case_ids()


def test_cases_are_json_compatible() -> None:
    assert json.loads(json.dumps(CASES)) == CASES


def test_spanned_days_include_dates_on_both_sides_of_midnight() -> None:
    data = timeline(activity("s1", -10, 10, app("A")))

    assert spanned_days(data) == [
        (date.fromisoformat(DAY) - timedelta(days=1)).isoformat(),
        DAY,
    ]
