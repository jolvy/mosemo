"""요구사항 인수 조건(AC-8001~8011, AC-8017~8021)을 참조 구현 호출로 옮긴다.

AC-8012(완료 요청 검증)와 AC-8013~8016·AC-8022·AC-8023(갱신·격리·저장)은 #268 범위 밖이다.
"""

import re
from pathlib import Path

from tests.support.analytics_reference import daily, session
from tests.support.analytics_timelines import (
    CHROME,
    CODING,
    DAY,
    LEISURE,
    MIN,
    NEXT_DAY,
    OPAQUE,
    PSL,
    SAFARI,
    SESSION,
    activity,
    app,
    browser,
    focus,
    gap,
    timeline,
    unknown_app,
    utc,
)

REQUIREMENTS = (
    Path(__file__).resolve().parents[4] / "docs/requirements/activity-analytics.md"
)
OUT_OF_SCOPE = {
    "AC-8012",
    "AC-8013",
    "AC-8014",
    "AC-8015",
    "AC-8016",
    "AC-8022",
    "AC-8023",
    "AC-8024",
    "AC-8025",
    "AC-8026",
    "AC-8027",
    "AC-8028",
    "AC-8029",
}
AC_IDS = [
    "AC-8001",
    "AC-8002",
    "AC-8003",
    "AC-8004",
    "AC-8005",
    "AC-8006",
    "AC-8007",
    "AC-8008",
    "AC-8009",
    "AC-8010",
    "AC-8011",
    "AC-8017",
    "AC-8018",
    "AC-8019",
    "AC-8020",
    "AC-8021",
]
YT = "https://m.youtube.com/watch"
SLACK = "com.tinyspeck.slackmacgap"


def test_ac_ids_match_requirements_in_scope() -> None:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    documented = set(re.findall(r"^- (AC-\d{4}):", text, flags=re.MULTILINE))

    assert set(AC_IDS) == documented - OUT_OF_SCOPE
    assert len(AC_IDS) == len(set(AC_IDS))


def test_every_ac_id_has_a_test() -> None:
    names = [name for name in globals() if name.startswith("test_ac_")]

    for ac in AC_IDS:
        assert any(name.startswith(f"test_ac_{ac[3:]}_") for name in names), ac


def test_ac_8001_usage_ranks_apps_and_targets_with_separate_totals() -> None:
    data = timeline(
        activity("s1", 0, 30, app("A")),
        activity("s2", 30, 50, browser(YT)),
        activity("s3", 50, 55, browser(None)),
        activity("s4", 55, 60, OPAQUE),
        activity("s5", 60, 62, unknown_app()),
    )

    c1 = daily(data, DAY, PSL)["c1"]

    assert c1 == {
        "apps": [{"key": "A", "us": 30 * MIN}, {"key": CHROME, "us": 25 * MIN}],
        "targets": [
            {"key": "A", "us": 30 * MIN},
            {"key": "youtube.com", "us": 20 * MIN},
            {"key": CHROME, "us": 5 * MIN},
        ],
        "unknown_app_us": 2 * MIN,
        "opaque_us": 5 * MIN,
        "observed_us": 62 * MIN,
    }


def test_ac_8002_usage_counts_overlap_and_open_segment_until_last_observed() -> None:
    data = timeline(
        activity("s1", 1430, 1450, app("A")),
        activity("s2", 1450, None, app("B"), last=1460),
    )

    first, second = daily(data, DAY, PSL)["c1"], daily(data, NEXT_DAY, PSL)["c1"]

    assert first["apps"] == [{"key": "A", "us": 10 * MIN}]
    assert second["apps"] == [
        {"key": "A", "us": 10 * MIN},
        {"key": "B", "us": 10 * MIN},
    ]


def test_ac_8003_usage_excludes_capture_gap_and_silence() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        gap("g1", 10, 30),
        activity("s2", 30, 40, app("A")),
        activity("s3", 50, 60, app("A")),
    )

    c1 = daily(data, DAY, PSL)["c1"]

    assert c1["observed_us"] == 30 * MIN
    assert c1["apps"] == [{"key": "A", "us": 30 * MIN}]


def test_ac_8004_target_transitions_carry_targets_time_and_dwell() -> None:
    data = timeline(
        activity("s1", 0, 5, app("A", "창 1")),
        activity("s2", 5, 10, app("A", "창 2")),
        activity("s3", 10, 12, app("B")),
        activity("s4", 12, 20, app("A")),
    )

    assert daily(data, DAY, PSL)["c2"] == [
        {"at": utc(10), "prev": "A", "next": "B", "dwell_us": 10 * MIN},
        {"at": utc(12), "prev": "B", "next": "A", "dwell_us": 2 * MIN},
    ]


def test_ac_8005_changes_across_gap_silence_opaque_or_unknown_are_not_counted() -> None:
    data = timeline(
        activity("s1", 0, 5, app("A")),
        gap("g1", 5, 10),
        activity("s2", 10, 15, app("B")),
        activity("s3", 16, 20, app("C")),
        activity("s4", 20, 25, OPAQUE),
        activity("s5", 25, 30, app("D")),
        activity("s6", 30, 35, unknown_app()),
        activity("s7", 35, 40, app("E")),
    )

    assert daily(data, DAY, PSL)["c2"] == []


def test_ac_8006_zero_second_segment_counts_two_transitions() -> None:
    data = timeline(
        activity("s1", 0, 5, app("A")),
        activity("s2", 5, 5, app("B")),
        activity("s3", 5, 10, app("C")),
    )

    assert len(daily(data, DAY, PSL)["c2"]) == 2


def test_ac_8007_session_time_splits_into_six_values_summing_to_analysis() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 20, app("B")),
        activity("s3", 20, 30, app("C")),
        activity("s4", 30, 40, app("D")),
        activity("s5", 40, 50, OPAQUE),
        labels={"s1": CODING, "s2": LEISURE, "s3": None},
        sessions=[focus()],
    )

    output = session(data, SESSION, PSL)
    c4 = output["c4"]

    assert [
        c4[name]
        for name in (
            "focus_us",
            "lapse_us",
            "unclassified_us",
            "pending_us",
            "opaque_us",
            "unobserved_us",
        )
    ] == [10 * MIN] * 6
    assert (
        sum(value for name, value in c4.items() if name.endswith("_us"))
        == (output["analysis_us"])
    )


def test_ac_8008_pending_inside_lapse_keeps_one_lapse_and_counts_both_entries() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 15, app("B")),
        activity("s3", 15, 20, app("X")),
        activity("s4", 20, 25, app("C")),
        activity("s5", 25, 60, app("A")),
        labels={"s1": CODING, "s2": LEISURE, "s4": LEISURE, "s5": CODING},
        sessions=[focus()],
    )

    output = session(data, SESSION, PSL)

    assert output["c4"]["lapse_count"] == 1
    assert output["c4"]["lapse_us"] == 10 * MIN
    assert output["c5"] == [
        {"key": "B", "entries": 1, "lapse_us": 5 * MIN},
        {"key": "C", "entries": 1, "lapse_us": 5 * MIN},
    ]


def test_ac_8009_lapse_evidence_has_every_field_and_no_opaque_information() -> None:
    entry = browser(YT)
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 15, entry),
        activity("s3", 15, 20, OPAQUE),
        activity("s4", 20, 25, app("B")),
        activity("s5", 25, 30, app("C")),
        activity("s6", 30, 60, app("A")),
        labels={
            "s1": CODING,
            "s2": LEISURE,
            "s3": LEISURE,
            "s4": LEISURE,
            "s5": LEISURE,
            "s6": CODING,
        },
        sessions=[focus()],
    )

    c6 = session(data, SESSION, PSL)["c6"]

    assert c6 == [
        {
            "start": utc(10),
            "end": utc(30),
            "entry_segment_id": "s2",
            "entry_target": "youtube.com",
            "entry_label_id": LEISURE,
            "entry_context": entry,
            "prev_focus_target": "A",
            "elapsed_us": 10 * MIN,
            "lapse_us": 15 * MIN,
            "transitions": 1,
            "returned": True,
        }
    ]
    assert "s3" not in repr(c6)


def test_ac_8010_paused_segments_and_transitions_are_excluded() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 15, app("B")),
        activity("s3", 15, 20, app("C")),
        activity("s4", 20, 60, app("A")),
        labels={"s1": CODING, "s2": LEISURE, "s3": LEISURE, "s4": CODING},
        sessions=[focus(pauses=[(10, 20)])],
    )

    output = session(data, SESSION, PSL)

    assert output["analysis_us"] == 50 * MIN
    assert output["c3"] == []
    assert output["c4"]["lapse_us"] == 0
    assert output["c4"]["lapse_count"] == 0
    assert output["c5"] == []


def test_ac_8011_session_without_pause_info_analyses_whole_span() -> None:
    data = timeline(
        activity("s1", 0, 60, app("A")),
        labels={"s1": CODING},
        sessions=[focus(pauses=None)],
    )

    output = session(data, SESSION, PSL)

    assert output["has_pause_info"] is False
    assert output["analysis_us"] == 60 * MIN
    assert output["c4"]["focus_us"] == 60 * MIN


def test_ac_8017_same_site_in_other_tab_or_browser_is_not_a_transition() -> None:
    data = timeline(
        activity("s1", 0, 5, browser("https://youtube.com/a")),
        activity("s2", 5, 10, browser("https://youtube.com/b")),
        activity("s3", 10, 15, browser("https://youtube.com/c", bundle_id=SAFARI)),
        activity("s4", 15, 20, browser("https://github.com/")),
    )

    c2 = daily(data, DAY, PSL)["c2"]

    assert [(row["prev"], row["next"]) for row in c2] == [("youtube.com", "github.com")]


def test_ac_8018_lapse_chain_counts_entries_and_inner_transitions() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 15, browser(YT)),
        activity("s3", 15, 20, app(SLACK)),
        activity("s4", 20, 25, browser(YT)),
        activity("s5", 25, 60, app("A")),
        labels={
            "s1": CODING,
            "s2": LEISURE,
            "s3": LEISURE,
            "s4": LEISURE,
            "s5": CODING,
        },
        sessions=[focus()],
    )

    output = session(data, SESSION, PSL)

    assert output["c4"]["lapse_count"] == 1
    assert {row["key"]: row["entries"] for row in output["c5"]} == {
        "youtube.com": 2,
        SLACK: 1,
    }
    assert output["c6"][0]["transitions"] == 2


def test_ac_8019_transitions_at_session_start_and_pause_end_are_excluded() -> None:
    data = timeline(
        activity("s1", -10, 0, app("A")),
        activity("s2", 0, 10, app("B")),
        activity("s3", 10, 30, app("C")),
        activity("s4", 30, 40, app("D")),
        sessions=[focus(pauses=[(5, 10)])],
    )

    c3 = session(data, SESSION, PSL)["c3"]

    assert [row["at"] for row in c3] == [utc(30)]


def test_ac_8020_label_transitions_count_zero_second_and_stop_at_pending() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 10, app("A")),
        activity("s3", 10, 20, app("A")),
        gap("g1", 20, 30),
        activity("s4", 30, 40, app("A")),
        activity("s5", 40, 50, app("B")),
        activity("s6", 50, 60, app("C")),
        labels={"s1": CODING, "s2": LEISURE, "s3": CODING, "s4": CODING, "s6": LEISURE},
    )

    c7 = daily(data, DAY, PSL)["c7"]

    assert [row["at"] for row in c7] == [utc(10), utc(10)]


def test_ac_8021_stale_confirmation_is_pending_and_unused_for_label_transition() -> (
    None
):
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 20, app("B"), version="v2"),
        labels={"s1": CODING},
        stale={"s2": LEISURE},
        sessions=[focus()],
    )

    output = session(data, SESSION, PSL)

    assert output["c4"]["pending_us"] == 10 * MIN
    assert output["c4"]["lapse_us"] == 0
    assert output["c7"] == []
    assert daily(data, DAY, PSL)["c7"] == []
