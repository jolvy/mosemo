"""참조 구현이 지표 명세 사례 표(`tests.support.analytics_cases.CASES`)의 기대값을 낸다(SC-1)."""

import json
from datetime import date, timedelta
from typing import Any

import pytest

from tests.support.analytics_cases import CASES, spanned_days, spec_case_ids
from tests.support.analytics_reference import daily, read_day, session
from tests.support.analytics_timelines import (
    DAY,
    PSL,
    activity,
    app,
    only,
    resolve,
    timeline,
)

CALLS: dict[str, Any] = {"daily": daily, "session": session, "read_day": read_day}


def run(case: dict) -> Any:
    return CALLS[case["call"]](case["data"], *case["args"])


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
    chosen = only(case["data"], read_day(case["data"], day, psl))

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
        chosen = only(data, read_day(data, day, PSL))
        assert daily(chosen, day, PSL) == daily(data, day, PSL), day


def test_cases_cover_every_spec_case_id() -> None:
    assert len(spec_case_ids()) == 98
    assert set(CASES) == spec_case_ids()


def test_cases_are_json_compatible() -> None:
    assert json.loads(json.dumps(CASES)) == CASES


def test_spanned_days_include_dates_on_both_sides_of_midnight() -> None:
    data = timeline(activity("s1", -10, 10, app("A")))

    assert spanned_days(data) == [
        (date.fromisoformat(DAY) - timedelta(days=1)).isoformat(),
        DAY,
    ]


@pytest.mark.parametrize(("case_id", "metric"), [("C2-B11", "c2"), ("C7-B9", "c7")])
def test_midnight_transition_is_not_on_the_first_day(case_id: str, metric: str) -> None:
    data = CASES[case_id]["data"]

    assert daily(data, DAY, PSL)[metric] == []
    assert daily(only(data, read_day(data, DAY, PSL)), DAY, PSL)[metric] == []
