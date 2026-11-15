"""MR-7: 제품 분석 계산 모듈에서 변형 관계 R1~R5와 P2가 성립한다(AC-8004·8005·8021·8022, E2, E6, E11).

관계 정의의 원본은 4단계 B 설계 검증의 `metamorphic.py`다. 입력 x를 x'로 바꾸고 같은 구현의
출력 f(x)와 f(x')이 지켜야 할 관계를 확인한다. 기대 변화는 입력과 f(x)에서만 끌어낸다. "같은
대상"이 필요한 곳은 bundleId·web.kind·URL 원문이 모두 같은 구간만 고른다.

원본 뒤에 일간 출력에 더해진 세션 합(`sessions`)·보조 값(`aux`, #279)의 기대도 함께 적었다.

- R1 같은 문맥 구간 분할: 모든 출력 같음(id는 `<id>~b` → `<id>`).
- R2 하루 평행이동: 시각만 +24시간(일광 절약 시간이 끼면 건너뜀).
- R3 식별자 치환: 출력 = 치환(원래 출력), 순위는 FR-4 순서로 다시 정렬.
- R4a 같은 대상 사이 0초 불투명 삽입: 체류가 삽입 시각에서 끊기고, 두 구간이 만들던 C7 행 하나가 빠진다.
- R4b 같은 대상 앞 구간 끝을 불투명으로 잘라 냄: C1 불투명 += 잘라 낸 시간, 앱·대상 순위의 키 하나만 줆.
- R5 확정 → 판정 대기: (가) 확정 삭제와 (나) version 변경의 출력이 같고, 확정이 쓰이던 곳만 바뀐다.
- P2 날짜 읽기: 그날 읽은 구간으로 계산한 일간 결과 = 전체 구간으로 계산한 일간 결과(AC-8022).
"""

import copy
import random
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from hypothesis import given
from hypothesis import strategies as st

from tests.support import analytics_product as impl
from tests.support.analytics_reference.daily import day_window
from tests.support.analytics_strategies import PSL, GeneratedTimeline, timelines

DAY = timedelta(days=1)
MICRO = timedelta(microseconds=1)
SHORT_DWELL_US = 5_000_000

Data = dict[str, Any]
Output = dict[str, Any]


def evaluate(data: Data, days: list[str], session_ids: list[str]) -> Output:
    return {
        "read_day": {day: impl.read_day(data, day, PSL) for day in days},
        "daily": {day: impl.daily(data, day, PSL) for day in days},
        "session": {sid: impl.session(data, sid, PSL) for sid in session_ids},
    }


def moment(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def window_of(data: Data, day: str) -> tuple[datetime, datetime]:
    return day_window(data["timezone"], day)


def effective_end(segment: Data) -> datetime:
    return at(segment.get("ended_at") or segment["last_observed_at"])


def is_activity(segment: Data) -> bool:
    return segment["segment_type"] == "activity"


def same_target_fields(before: Data, after: Data) -> bool:
    if not (is_activity(before) and is_activity(after)):
        return False
    first, second = before["context"], after["context"]
    if first["kind"] != "detailed" or second["kind"] != "detailed":
        return False
    bundle = first["app"]["bundleId"]
    if bundle.get("status") != "captured" or bundle != second["app"]["bundleId"]:
        return False
    web, other = first.get("web") or {}, second.get("web") or {}
    if web.get("kind") != other.get("kind"):
        return False
    return web.get("kind") != "browser" or web.get("url") == other.get("url")


def adjacent(before: Data, after: Data) -> bool:
    return (
        before["segment_type"] != "capture_gap"
        and after["segment_type"] != "capture_gap"
        and before.get("ended_at") is not None
        and at(before["ended_at"]) == at(after["started_at"])
    )


def valid_label(data: Data, segment: Data) -> tuple[bool, str | None]:
    """유효한 확정 라벨. 없으면 (False, None), 미분류면 (True, None)."""
    if not is_activity(segment) or segment["context"]["kind"] == "opaque":
        return False, None
    for item in data["confirmations"]:
        if item["first_event_id"] == segment["first_event_id"]:
            return item["segment_version"] == segment["version"], item["label_id"]
    return False, None


def label_row(data: Data, before: Data, after: Data) -> Data | None:
    """맞닿은 이웃이 만드는 라벨 전환 행(FR-22·23)."""
    if not adjacent(before, after):
        return None
    valid_before, label_before = valid_label(data, before)
    valid_after, label_after = valid_label(data, after)
    if not (valid_before and valid_after) or label_before is None:
        return None
    if label_after is None or label_before == label_after:
        return None
    return {
        "at": utc_iso(at(after["started_at"])),
        "prev": label_before,
        "next": label_after,
    }


def label_row_at(data: Data, index: int) -> tuple[Data, int] | None:
    """쌍 (index, index+1)의 라벨 전환 행과, 앞 쌍들이 만든 같은 내용 행의 수."""
    segments = data["segments"]
    row = label_row(data, segments[index], segments[index + 1])
    if row is None:
        return None
    earlier = sum(
        1
        for other in range(index)
        if label_row(data, segments[other], segments[other + 1]) == row
    )
    return row, earlier


def remove_rows(rows: list[Data], drop: list[tuple[Data, int]]) -> list[Data] | None:
    """같은 내용의 행 중 k번째를 지운다. 있어야 할 행이 없으면 None."""
    rows = list(rows)
    for row, k in sorted(drop, key=lambda item: -item[1]):
        positions = [index for index, item in enumerate(rows) if item == row]
        if len(positions) <= k:
            return None
        del rows[positions[k]]
    return rows


def work_intervals(session: Data) -> list[tuple[datetime, datetime]]:
    start, end = at(session["started_at"]), at(session["ended_at"])
    intervals, cursor = [], start
    for low, high in sorted((at(a), at(b)) for a, b in session["pauses"] or []):
        if low > cursor:
            intervals.append((cursor, low))
        cursor = max(cursor, high)
    if end > cursor:
        intervals.append((cursor, end))
    return intervals


def inside_work(session: Data, value: str) -> bool:
    moment_ = at(value)
    return any(low < moment_ < high for low, high in work_intervals(session))


def overlap_us(
    first: tuple[datetime, datetime], second: tuple[datetime, datetime]
) -> int:
    return max(0, (min(first[1], second[1]) - max(first[0], second[0])) // MICRO)


def boundary_points(timeline: GeneratedTimeline) -> list[datetime]:
    data = timeline.data
    points = {window_of(data, day)[0] for day in timeline.days}
    for session in data["sessions"]:
        points |= {at(session["started_at"]), at(session["ended_at"])}
        points |= {at(value) for pause in session["pauses"] or [] for value in pause}
    return sorted(points)


def short_dwell(rows: list[Data]) -> int:
    return sum(1 for row in rows if row["dwell_us"] < SHORT_DWELL_US)


def check(name: str, got: object, want: object, violations: list[str]) -> None:
    if got != want:
        violations.append(f"{name}: got={got!r} want={want!r}")


@given(timelines(), st.randoms(use_true_random=False))
def test_r1_splitting_a_segment_in_the_same_context_changes_nothing(
    timeline: GeneratedTimeline, rnd: random.Random
) -> None:
    data, days, sids = timeline.data, timeline.days, timeline.session_ids
    candidates = [i for i, s in enumerate(data["segments"]) if is_activity(s)]
    if not candidates:
        return
    original = evaluate(data, days, sids)
    index = rnd.choice(candidates)
    segment = data["segments"][index]
    start, end = at(segment["started_at"]), effective_end(segment)
    points = [p for p in boundary_points(timeline) if start < p < end]
    roll = rnd.random()
    if roll < 0.3 or end == start:
        split = start
    elif roll < 0.65 and points:
        split = rnd.choice(points)
    else:
        split = start + (end - start) * rnd.random()
    changed = copy.deepcopy(data)
    front = changed["segments"][index]
    back = copy.deepcopy(front)
    front["ended_at"] = front["last_observed_at"] = iso(split)
    back["id"] = front["id"] + "~b"
    back["first_event_id"] = front["first_event_id"] + "~b"
    back["started_at"] = iso(split)
    changed["segments"].insert(index + 1, back)
    changed["confirmations"] += [
        {**item, "first_event_id": back["first_event_id"]}
        for item in data["confirmations"]
        if item["first_event_id"] == front["first_event_id"]
    ]
    result = evaluate(changed, days, sids)

    def unsplit(value: str) -> str:
        return value.removesuffix("~b")

    violations: list[str] = []
    for day in days:
        ids = list(dict.fromkeys(unsplit(i) for i in result["read_day"][day]))
        check(f"read_day {day}", ids, original["read_day"][day], violations)
        check(f"daily {day}", result["daily"][day], original["daily"][day], violations)
    for sid in sids:
        got = copy.deepcopy(result["session"][sid])
        for row in got["c6"]:
            row["entry_segment_id"] = unsplit(row["entry_segment_id"])
        check(f"session {sid}", got, original["session"][sid], violations)
    assert violations == [], (segment["id"], iso(split))


def shifted(value: str) -> str:
    return utc_iso(at(value) + DAY)


def shift_output(output: Output) -> Output:
    output = copy.deepcopy(output)
    for key in ("c2", "c3", "c7", "c6"):
        for row in output.get(key, []):
            for field in ("at", "start", "end"):
                if row.get(field) is not None:
                    row[field] = shifted(row[field])
    return output


def all_times(data: Data) -> list[datetime]:
    times = [
        at(segment[key])
        for segment in data["segments"]
        for key in ("started_at", "ended_at", "last_observed_at")
        if segment.get(key)
    ]
    for session in data["sessions"]:
        times += [at(session["started_at"]), at(session["ended_at"])]
        times += [at(value) for pause in session["pauses"] or [] for value in pause]
    return times


@given(timelines())
def test_r2_shifting_by_one_day_shifts_only_the_times(
    timeline: GeneratedTimeline,
) -> None:
    data, days, sids = timeline.data, timeline.days, timeline.session_ids
    zone = ZoneInfo(data["timezone"])
    edges = [edge for day in days for edge in window_of(data, day)]
    if any(
        t.astimezone(zone).utcoffset() != (t + DAY).astimezone(zone).utcoffset()
        for t in all_times(data) + edges
    ):
        return  # 일광 절약 시간 전환이 끼면 하루가 23·25시간이라 관계가 없다.
    original = evaluate(data, days, sids)
    changed = copy.deepcopy(data)
    for segment in changed["segments"]:
        for key in ("started_at", "ended_at", "last_observed_at"):
            if segment.get(key):
                segment[key] = iso(at(segment[key]) + DAY)
    for session in changed["sessions"]:
        session["started_at"] = iso(at(session["started_at"]) + DAY)
        session["ended_at"] = iso(at(session["ended_at"]) + DAY)
        if session["pauses"] is not None:
            session["pauses"] = [
                [iso(at(low) + DAY), iso(at(high) + DAY)]
                for low, high in session["pauses"]
            ]
    next_days = [(datetime.fromisoformat(d) + DAY).date().isoformat() for d in days]
    result = evaluate(changed, next_days, sids)

    for day, next_day in zip(days, next_days, strict=True):
        assert result["read_day"][next_day] == original["read_day"][day]
        assert result["daily"][next_day] == shift_output(original["daily"][day])
    for sid in sids:
        assert result["session"][sid] == shift_output(original["session"][sid])


def ranked(rows: list[Data], field: str) -> list[Data]:
    return sorted(rows, key=lambda row: (-row[field], row["key"]))


@given(timelines(), st.randoms(use_true_random=False))
def test_r3_renaming_identifiers_renames_the_output(
    timeline: GeneratedTimeline, rnd: random.Random
) -> None:
    data, days, sids = timeline.data, timeline.days, timeline.session_ids
    original = evaluate(data, days, sids)

    def fresh(prefix: str, values: set[str]) -> dict[str, str]:
        ordered = sorted(values)
        names = [f"{prefix}{rnd.randrange(10**6):06d}.{j}" for j in range(len(ordered))]
        rnd.shuffle(names)
        return dict(zip(ordered, names, strict=True))

    bundles: set[str] = set()
    labels: set[str] = set()
    versions: set[str] = set()
    for segment in data["segments"]:
        if is_activity(segment):
            versions.add(segment["version"])
            bundle = segment["context"].get("app", {}).get("bundleId", {})
            if (
                bundle.get("status") == "captured"
                and segment["context"]["kind"] != "opaque"
            ):
                bundles.add(bundle["value"])
    for item in data["confirmations"]:
        versions.add(item["segment_version"])
        if item["label_id"] is not None:
            labels.add(item["label_id"])
    labels |= {session["label_id"] for session in data["sessions"]}
    bundle_map = fresh("zz.app", bundles)
    label_map = fresh("M-", labels)
    version_map = fresh("ver", versions)
    segment_ids = {
        s["id"]: f"q{j}-{rnd.randrange(1000)}" for j, s in enumerate(data["segments"])
    }
    event_ids = {s["first_event_id"]: f"ev{j}" for j, s in enumerate(data["segments"])}
    session_ids = {
        s["id"]: f"F{j}-{rnd.randrange(1000)}" for j, s in enumerate(data["sessions"])
    }

    def rename_context(context: Data) -> Data:
        context = copy.deepcopy(context)
        bundle = context.get("app", {}).get("bundleId", {})
        if context["kind"] != "opaque" and bundle.get("status") == "captured":
            bundle["value"] = bundle_map[bundle["value"]]
        return context

    changed = copy.deepcopy(data)
    for segment in changed["segments"]:
        segment["id"] = segment_ids[segment["id"]]
        segment["first_event_id"] = event_ids[segment["first_event_id"]]
        if is_activity(segment):
            segment["version"] = version_map[segment["version"]]
            segment["context"] = rename_context(segment["context"])
    for item in changed["confirmations"]:
        item["first_event_id"] = event_ids[item["first_event_id"]]
        item["segment_version"] = version_map[item["segment_version"]]
        if item["label_id"] is not None:
            item["label_id"] = label_map[item["label_id"]]
    for session in changed["sessions"]:
        session["id"] = session_ids[session["id"]]
        session["label_id"] = label_map[session["label_id"]]
    result = evaluate(changed, days, [session_ids[sid] for sid in sids])

    def target(key: str | None) -> str | None:
        return None if key is None else bundle_map.get(key, key)

    def label(key: str | None) -> str | None:
        return None if key is None else label_map.get(key, key)

    def rename_rows(rows: list[Data], name: Any) -> list[Data]:
        return [{**r, "prev": name(r["prev"]), "next": name(r["next"])} for r in rows]

    def rename_distractions(rows: list[Data]) -> list[Data]:
        return ranked([{**r, "key": target(r["key"])} for r in rows], "lapse_us")

    def rename_daily(output: Output) -> Output:
        output = copy.deepcopy(output)
        c1 = output["c1"]
        c1["apps"] = ranked([{**r, "key": target(r["key"])} for r in c1["apps"]], "us")
        c1["targets"] = ranked(
            [{**r, "key": target(r["key"])} for r in c1["targets"]], "us"
        )
        output["c2"] = rename_rows(output["c2"], target)
        output["c7"] = rename_rows(output["c7"], label)
        sessions = output["sessions"]
        sessions["distractions"] = rename_distractions(sessions["distractions"])
        return output

    def rename_session(output: Output) -> Output:
        output = copy.deepcopy(output)
        output["c3"] = rename_rows(output["c3"], target)
        output["c7"] = rename_rows(output["c7"], label)
        output["c5"] = rename_distractions(output["c5"])
        for row in output["c6"]:
            row["entry_segment_id"] = segment_ids[row["entry_segment_id"]]
            row["entry_target"] = target(row["entry_target"])
            row["entry_label_id"] = label(row["entry_label_id"])
            row["entry_context"] = rename_context(row["entry_context"])
            row["prev_focus_target"] = target(row["prev_focus_target"])
        return output

    for day in days:
        assert result["read_day"][day] == [
            segment_ids[i] for i in original["read_day"][day]
        ]
        assert result["daily"][day] == rename_daily(original["daily"][day])
    for sid in sids:
        assert result["session"][session_ids[sid]] == rename_session(
            original["session"][sid]
        )


def same_target_pairs(data: Data) -> list[int]:
    segments = data["segments"]
    return [
        i
        for i in range(len(segments) - 1)
        if adjacent(segments[i], segments[i + 1])
        and same_target_fields(segments[i], segments[i + 1])
        and at(segments[i]["ended_at"]) > at(segments[i]["started_at"])
    ]


def dwell_cut(rows: list[Data], cut: datetime) -> list[Data]:
    """체류가 `cut`을 지나는 행은 `cut`에서 끊긴다."""
    result = []
    for row in rows:
        moment_ = at(row["at"])
        if moment_ >= cut and moment_ - row["dwell_us"] * MICRO <= cut:
            row = {**row, "dwell_us": (moment_ - cut) // MICRO}
        result.append(row)
    return result


def one_key_minus(old: list[Data], new: list[Data], decrease: int, field: str) -> bool:
    """키 하나만 `decrease` 줄고(0이면 행 삭제) 나머지 같다."""
    before = {row["key"]: row[field] for row in old}
    after = {row["key"]: row[field] for row in new}
    if decrease == 0:
        return old == new
    changed = [
        k for k in before.keys() | after.keys() if before.get(k, 0) != after.get(k, 0)
    ]
    return (
        len(changed) == 1
        and before.get(changed[0], 0) - after.get(changed[0], 0) == decrease
        and new == ranked(new, field)
    )


def opaque_segment(index: int, *, start: datetime, end: datetime) -> Data:
    return {
        "id": f"op{index}",
        "segment_type": "activity",
        "started_at": iso(start),
        "ended_at": iso(end),
        "last_observed_at": iso(end),
        "first_event_id": f"opev{index}",
        "version": "v1",
        "context": {"kind": "opaque"},
    }


@given(timelines(), st.randoms(use_true_random=False))
def test_r4a_zero_length_opaque_between_same_targets_cuts_only_the_run(
    timeline: GeneratedTimeline, rnd: random.Random
) -> None:
    data, days, sids = timeline.data, timeline.days, timeline.session_ids
    pairs = same_target_pairs(data)
    if not pairs:
        return
    original = evaluate(data, days, sids)
    index = rnd.choice(pairs)
    cut = at(data["segments"][index]["ended_at"])
    changed = copy.deepcopy(data)
    inserted = opaque_segment(index, start=cut, end=cut)
    changed["segments"].insert(index + 1, inserted)
    result = evaluate(changed, days, sids)
    label = label_row_at(data, index)

    for day in days:
        low, high = window_of(data, day)
        want = copy.deepcopy(original["daily"][day])
        want["c2"] = dwell_cut(want["c2"], cut)
        want["aux"]["short_dwell_transition_count"] = short_dwell(want["c2"])
        if label is not None and low <= at(label[0]["at"]) < high:
            want["c7"] = remove_rows(want["c7"], [label])
        assert result["daily"][day] == want, day
        read = [i for i in result["read_day"][day] if i != inserted["id"]]
        assert set(read) <= set(original["read_day"][day]), day
    for sid in sids:
        session = next(s for s in data["sessions"] if s["id"] == sid)
        want = copy.deepcopy(original["session"][sid])
        want["c3"] = dwell_cut(want["c3"], cut)
        if label is not None and inside_work(session, label[0]["at"]):
            want["c7"] = remove_rows(want["c7"], [label])
        assert result["session"][sid] == want, sid


@given(timelines(), st.randoms(use_true_random=False))
def test_r4b_carving_opaque_out_of_the_first_segment_moves_only_that_time(
    timeline: GeneratedTimeline, rnd: random.Random
) -> None:
    data, days = timeline.data, timeline.days
    pairs = same_target_pairs(data)
    if not pairs:
        return
    original = evaluate(data, days, [])
    index = rnd.choice(pairs)
    first = data["segments"][index]
    start, cut = at(first["started_at"]), at(first["ended_at"])
    carved = max((cut - start) * rnd.choice([0.001, 0.5, 0.999]), MICRO)
    if carved >= cut - start:
        return
    changed = copy.deepcopy(data)
    changed["segments"][index]["ended_at"] = iso(cut - carved)
    changed["segments"][index]["last_observed_at"] = iso(cut - carved)
    changed["segments"].insert(
        index + 1, opaque_segment(index, start=cut - carved, end=cut)
    )
    result = evaluate(changed, days, [])
    label = label_row_at(data, index)
    pending = not valid_label(data, first)[0]

    for day in days:
        window = window_of(data, day)
        carved_us = overlap_us((cut - carved, cut), window)
        want = copy.deepcopy(original["daily"][day])
        got = copy.deepcopy(result["daily"][day])
        want["c2"] = dwell_cut(want["c2"], cut)
        if label is not None and window[0] <= at(label[0]["at"]) < window[1]:
            want["c7"] = remove_rows(want["c7"], [label])
        old_c1, new_c1 = want.pop("c1"), got.pop("c1")
        assert new_c1["opaque_us"] == old_c1["opaque_us"] + carved_us, day
        assert new_c1["observed_us"] == old_c1["observed_us"], day
        assert new_c1["unknown_app_us"] == old_c1["unknown_app_us"], day
        assert one_key_minus(old_c1["apps"], new_c1["apps"], carved_us, "us"), day
        assert one_key_minus(old_c1["targets"], new_c1["targets"], carved_us, "us"), day
        old_aux, new_aux = want.pop("aux"), got.pop("aux")
        assert new_aux["short_dwell_transition_count"] == short_dwell(want["c2"]), day
        assert new_aux["pending_us"] == old_aux["pending_us"] - carved_us * pending
        assert old_aux["browser_without_url_us"] - new_aux[
            "browser_without_url_us"
        ] in {
            0,
            carved_us,
        }
        assert (new_aux["capture_gap_count"], new_aux["silence_count"]) == (
            old_aux["capture_gap_count"],
            old_aux["silence_count"],
        )
        # 세션 합은 C4 판정이 바뀌므로 세션 쪽 관계(R5)에서 본다.
        want.pop("sessions")
        got.pop("sessions")
        assert got == want, day


def check_session_c5(
    old: list[Data],
    new: list[Data],
    *,
    verdict: str,
    overlap: int,
    lapse_count: int,
    violations: list[str],
) -> None:
    """R5 (D11): 확정 하나를 판정 대기로 바꿨을 때 C5가 바뀔 수 있는 범위."""
    if sum(row["entries"] for row in new) < lapse_count:
        violations.append("c5: 진입 합 < 이탈 건수(D11 불변식)")
    if verdict == "unclassified_us":
        check("c5", new, old, violations)
        return
    before = {row["key"]: row for row in old}
    after = {row["key"]: row for row in new}
    ok = new == ranked(new, "lapse_us")
    lapse_changed = [
        key
        for key in before.keys() | after.keys()
        if (key in before) != (key in after)
        or before[key]["lapse_us"] != after[key]["lapse_us"]
    ]
    if verdict == "focus_us":
        ok = ok and not lapse_changed
    elif lapse_changed:
        old_row, new_row = before.get(lapse_changed[0]), after.get(lapse_changed[0])
        ok = (
            ok
            and len(lapse_changed) == 1
            and old_row is not None
            and old_row["lapse_us"] - (new_row["lapse_us"] if new_row else 0) == overlap
        )
    else:
        ok = ok and overlap == 0
    entries = {
        key: (after[key]["entries"] if key in after else 0)
        - (before[key]["entries"] if key in before else 0)
        for key in before.keys() | after.keys()
    }
    ok = ok and all(value in {-1, 0, 1} for value in entries.values())
    ok = ok and sum(1 for value in entries.values() if value == 1) <= 1
    if verdict == "focus_us":
        ok = ok and all(value <= 0 for value in entries.values())
        ok = ok and sum(1 for value in entries.values() if value) <= 1
    else:
        ok = ok and sum(entries.values()) in {-1, 0}
    if not ok:
        violations.append(f"c5: {old} → {new} (overlap {overlap}, {verdict})")


@given(timelines(), st.randoms(use_true_random=False))
def test_r5_dropping_a_confirmation_moves_only_its_time_to_pending(
    timeline: GeneratedTimeline, rnd: random.Random
) -> None:
    data, days, sids = timeline.data, timeline.days, timeline.session_ids
    confirmed = {item["first_event_id"] for item in data["confirmations"]}
    candidates = [
        i
        for i, s in enumerate(data["segments"])
        if is_activity(s) and s["first_event_id"] in confirmed
    ]
    if not candidates:
        return
    original = evaluate(data, days, sids)
    index = rnd.choice(candidates)
    segment = data["segments"][index]
    dropped = copy.deepcopy(data)
    dropped["confirmations"] = [
        item
        for item in dropped["confirmations"]
        if item["first_event_id"] != segment["first_event_id"]
    ]
    bumped = copy.deepcopy(data)
    bumped["segments"][index]["version"] = segment["version"] + "+1"
    result = evaluate(dropped, days, sids)

    assert evaluate(bumped, days, sids) == result
    valid, label = valid_label(data, segment)
    if not valid:
        assert result == original
        return
    segments = data["segments"]
    labels = [
        row
        for row in (
            label_row_at(data, index - 1) if index > 0 else None,
            label_row_at(data, index) if index + 1 < len(segments) else None,
        )
        if row is not None
    ]
    span = (at(segment["started_at"]), effective_end(segment))
    violations: list[str] = []
    for day in days:
        low, high = window = window_of(data, day)
        check(
            f"read_day {day}",
            result["read_day"][day],
            original["read_day"][day],
            violations,
        )
        want = copy.deepcopy(original["daily"][day])
        got = copy.deepcopy(result["daily"][day])
        want["c7"] = remove_rows(
            want["c7"], [r for r in labels if low <= at(r[0]["at"]) < high]
        )
        want["aux"]["pending_us"] += overlap_us(span, window)
        want.pop("sessions")
        got.pop("sessions")
        check(f"daily {day}", got, want, violations)
    for sid in sids:
        session = next(s for s in data["sessions"] if s["id"] == sid)
        old, new = original["session"][sid], result["session"][sid]
        verdict = (
            "unclassified_us"
            if label is None
            else ("focus_us" if label == session["label_id"] else "lapse_us")
        )
        overlap = sum(
            overlap_us(span, interval) for interval in work_intervals(session)
        )
        for field in ("has_pause_info", "analysis_us", "c3"):
            check(f"session {sid} {field}", new[field], old[field], violations)
        want_c7 = remove_rows(
            old["c7"], [r for r in labels if inside_work(session, r[0]["at"])]
        )
        check(f"session {sid} c7", new["c7"], want_c7, violations)
        count = old["c4"]["lapse_count"]
        allowed = {count} if verdict == "unclassified_us" else {count, count - 1}
        if new["c4"]["lapse_count"] not in allowed:
            violations.append(f"session {sid}: lapse_count {count} → {new['c4']}")
        want_c4 = dict(old["c4"])
        want_c4[verdict] -= overlap
        want_c4["pending_us"] += overlap
        want_c4["lapse_count"] = new["c4"]["lapse_count"]
        check(f"session {sid} c4", new["c4"], want_c4, violations)
        check_session_c5(
            old["c5"],
            new["c5"],
            verdict=verdict,
            overlap=overlap,
            lapse_count=new["c4"]["lapse_count"],
            violations=violations,
        )
        if verdict == "unclassified_us":
            check(f"session {sid} c6", new["c6"], old["c6"], violations)
            continue
        if len(new["c6"]) not in {len(old["c6"]), len(old["c6"]) - 1}:
            violations.append(
                f"session {sid}: c6 행 수 {len(old['c6'])} → {len(new['c6'])}"
            )
        decrease = overlap if verdict == "lapse_us" else 0
        if (
            sum(r["lapse_us"] for r in new["c6"])
            != sum(r["lapse_us"] for r in old["c6"]) - decrease
        ):
            violations.append(f"session {sid}: c6 lapse_us 합이 {decrease} 줄지 않음")
        check(
            f"session {sid} c6(end < g 시작)",
            [r for r in new["c6"] if at(r["end"]) < span[0]],
            [r for r in old["c6"] if at(r["end"]) < span[0]],
            violations,
        )
    assert violations == [], segment["id"]


@given(timelines())
def test_p2_daily_stats_on_segments_read_for_the_day_equal_full(
    timeline: GeneratedTimeline,
) -> None:
    data = timeline.data
    for day in timeline.days:
        keep = set(impl.read_day(data, day, PSL))
        chosen = {**data, "segments": [s for s in data["segments"] if s["id"] in keep]}
        assert impl.daily(chosen, day, PSL) == impl.daily(data, day, PSL), day
