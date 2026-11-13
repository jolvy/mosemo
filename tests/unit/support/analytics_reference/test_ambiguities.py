"""#268 「알려진 모호점」 1~7과 그 밖의 해석을 고정한다.

고른 해석과 근거는 `tests/support/analytics_reference/__init__.py` 「명세 해석」에 있다.
"""

import re

from tests.support.analytics_reference import daily, read_day, session
from tests.support.analytics_timelines import (
    CODING,
    DAY,
    LEISURE,
    MIN,
    NEXT_DAY,
    OPAQUE,
    PSL,
    SESSION,
    activity,
    app,
    browser,
    focus,
    gap,
    only,
    timeline,
    unknown_app,
    utc,
)


def targets(*urls: str) -> list[str]:
    data = timeline(
        *(
            activity(f"s{index}", index * 10, index * 10 + 10 - index, browser(url))
            for index, url in enumerate(urls)
        )
    )
    return [row["key"] for row in daily(data, DAY, PSL)["c1"]["targets"]]


def test_ambiguity_1_host_case_and_trailing_dot_are_normalized() -> None:
    assert targets("https://M.YouTube.COM./watch", "https://youtube.com/") == [
        "youtube.com"
    ]


def test_ambiguity_1_punycode_and_unicode_hosts_are_one_unicode_site() -> None:
    assert targets(
        "https://www.xn--vv4b11d.xn--3e0b707e/", "https://shop.예시.한국/"
    ) == ["예시.한국"]


def test_ambiguity_1_ipv6_host_is_kept_without_brackets() -> None:
    assert targets("http://[::1]:8080/") == ["::1"]


def test_ambiguity_2_read_day_reads_capture_gaps_by_the_same_condition() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        gap("g1", 10, 20),
        activity("s2", 20, 30, app("B")),
        gap("g2", 30, None),
    )

    assert read_day(data, DAY) == ["s1", "g1", "s2", "g2"]
    assert read_day(data, NEXT_DAY) == ["g2"]


def test_ambiguity_2_zero_second_gap_keeps_neighbours_apart_on_read() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        gap("g1", 10, 10),
        activity("s2", 10, 20, app("B")),
    )

    assert "g1" in read_day(data, DAY)
    assert daily(only(data, read_day(data, DAY)), DAY, PSL)["c2"] == []


def zero_second_lapses() -> dict:
    return timeline(
        activity("s1", 0, 0, app("X")),
        activity("s2", 0, 20, app("A")),
        activity("s3", 20, 20, app("Y")),
        activity("s4", 30, 30, app("Z")),
        activity("s5", 30, 60, app("A")),
        activity("s6", 60, 60, app("W")),
        labels={
            "s1": LEISURE,
            "s2": CODING,
            "s3": LEISURE,
            "s4": LEISURE,
            "s5": CODING,
            "s6": LEISURE,
        },
        sessions=[focus(pauses=[(20, 30)])],
    )


def test_ambiguity_3_zero_second_segment_counts_at_session_start_and_pause_end() -> (
    None
):
    output = session(zero_second_lapses(), SESSION, PSL)

    assert output["c4"]["lapse_count"] == 2
    assert [row["key"] for row in output["c5"]] == ["X", "Z"]
    assert [row["entry_segment_id"] for row in output["c6"]] == ["s1", "s4"]


def test_ambiguity_4_transitions_in_or_at_pause_are_not_lapse_transitions() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 20, app("B")),
        activity("s3", 20, 25, app("C")),
        activity("s4", 25, 30, app("D")),
        activity("s5", 30, 40, app("E")),
        activity("s6", 40, 50, app("F")),
        activity("s7", 50, 60, app("A")),
        labels={
            "s1": CODING,
            "s2": LEISURE,
            "s3": LEISURE,
            "s4": LEISURE,
            "s5": LEISURE,
            "s6": LEISURE,
            "s7": CODING,
        },
        sessions=[focus(pauses=[(20, 30)])],
    )

    output = session(data, SESSION, PSL)

    assert output["c6"][0]["transitions"] == 1
    assert [row["at"] for row in output["c3"]] == [utc(10), utc(40), utc(50)]


def test_ambiguity_5_previous_piece_looks_across_pause_and_unobserved_time() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 20, app("B")),
        activity("s3", 30, 35, app("B")),
        activity("s4", 40, 45, app("B")),
        activity("s5", 45, 60, app("A")),
        labels={
            "s1": CODING,
            "s2": LEISURE,
            "s3": LEISURE,
            "s4": LEISURE,
            "s5": CODING,
        },
        sessions=[focus(pauses=[(20, 30)])],
    )

    c5 = session(data, SESSION, PSL)["c5"]

    assert c5 == [{"key": "B", "entries": 1, "lapse_us": 20 * MIN}]


def test_ambiguity_6_unknown_app_to_unknown_app_is_not_a_new_entry() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 20, unknown_app("가")),
        activity("s3", 20, 30, unknown_app("나")),
        activity("s4", 30, 60, app("A")),
        labels={"s1": CODING, "s2": LEISURE, "s3": LEISURE, "s4": CODING},
        sessions=[focus()],
    )

    c5 = session(data, SESSION, PSL)["c5"]

    assert c5 == [{"key": "앱 미확인", "entries": 1, "lapse_us": 20 * MIN}]


def test_ambiguity_7_open_segment_counts_until_last_observed_in_session() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, None, app("B"), last=20),
        labels={"s1": CODING, "s2": LEISURE},
        sessions=[focus()],
    )

    output = session(data, SESSION, PSL)

    assert output["c4"]["lapse_us"] == 10 * MIN
    assert output["c4"]["unobserved_us"] == 40 * MIN
    assert output["c6"][0]["lapse_us"] == 10 * MIN
    assert output["c6"][0]["end"] == utc(60)
    assert output["c6"][0]["returned"] is False


def test_every_known_ambiguity_has_a_test() -> None:
    numbers = {
        int(match.group(1))
        for name in globals()
        if (match := re.match(r"test_ambiguity_(\d+)_", name))
    }

    assert numbers == set(range(1, 8))


def test_read_day_reads_back_possible_same_site_without_psl() -> None:
    data = timeline(
        activity("s1", -30, -10, browser("https://m.youtube.com/")),
        activity("s2", -10, 10, browser("https://www.youtube.com/")),
        activity("s3", 10, 20, app("A")),
    )

    assert read_day(data, DAY) == ["s1", "s2", "s3"]
    assert daily(data, DAY, PSL)["c2"][0]["dwell_us"] == 40 * MIN


def test_read_day_stops_at_a_different_app() -> None:
    data = timeline(
        activity("s1", -30, -10, app("B")),
        activity("s2", -10, 10, app("A")),
    )

    assert read_day(data, DAY) == ["s2"]


def test_lapse_start_on_same_target_as_focus_is_an_entry() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 20, app("A")),
        activity("s3", 20, 60, app("A")),
        labels={"s1": CODING, "s2": LEISURE, "s3": CODING},
        sessions=[focus()],
    )

    output = session(data, SESSION, PSL)

    assert output["c5"] == [{"key": "A", "entries": 1, "lapse_us": 10 * MIN}]
    assert output["c4"]["lapse_count"] == 1


def test_confirmed_opaque_segment_stays_opaque_and_has_no_label() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 20, OPAQUE),
        activity("s3", 20, 30, app("A")),
        labels={"s1": CODING, "s2": LEISURE, "s3": CODING},
        sessions=[focus()],
    )

    output = session(data, SESSION, PSL)

    assert output["c4"]["opaque_us"] == 10 * MIN
    assert output["c4"]["lapse_count"] == 0
    assert daily(data, DAY, PSL)["c7"] == []


def test_previous_focus_target_skips_other_verdicts() -> None:
    data = timeline(
        activity("s1", 0, 10, app("A")),
        activity("s2", 10, 20, app("P")),
        activity("s3", 20, 30, app("B")),
        activity("s4", 30, 60, app("A")),
        labels={"s1": CODING, "s3": LEISURE, "s4": CODING},
        sessions=[focus()],
    )

    assert session(data, SESSION, PSL)["c6"][0]["prev_focus_target"] == "A"


def test_lapse_starting_before_session_is_clipped_to_session_start() -> None:
    data = timeline(
        activity("s1", -10, 10, app("B")),
        activity("s2", 10, 60, app("A")),
        labels={"s1": LEISURE, "s2": CODING},
        sessions=[focus()],
    )

    row = session(data, SESSION, PSL)["c6"][0]

    assert (row["start"], row["elapsed_us"], row["lapse_us"]) == (utc(0), 0, 10 * MIN)


def test_browser_without_bundle_id_is_unknown_app() -> None:
    context = browser("https://youtube.com/")
    context["app"]["bundleId"] = {"status": "absent"}
    data = timeline(activity("s1", 0, 10, context))

    c1 = daily(data, DAY, PSL)["c1"]

    assert (c1["targets"], c1["unknown_app_us"]) == ([], 10 * MIN)
