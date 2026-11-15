"""활동 분석 무작위 타임라인의 Hypothesis 전략(SC-8·DF-5·MR-7).

설계 검증 생성기(`gen.py`, 4단계)의 분포를 옮겼다. 경계 시각(자정, 세션 시작·끝, 일시중지
시작·끝)을 먼저 정하고 구간 끝을 그 시각에 자주 맞춰 경계에 놓인 구간·0초 구간을 과대 생성한다.
침묵, 수집 공백(끝 없는 공백 포함), 불투명, 앱 미확인, IDN·IP·scheme 없는 URL, 일광 절약 시간
전환일(23·25시간), 자정을 넘는 세션, 일시중지로 끝나는 세션도 만든다.

`timelines()`는 같은 타임라인을 참조 구현 입력(`data`)과 제품 입력(`snapshot`)으로 함께 준다.
"""

import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from hypothesis import strategies as st

from mosemo.analytics.domain.source import SourceSnapshot
from tests.support.analytics_product import snapshot

SECOND = timedelta(seconds=1)

# 시험용 공개 접미사 목록. 규칙은 https://publicsuffix.org/list/public_suffix_list.dat 에서 옮겼다.
PSL = """// ===BEGIN ICANN DOMAINS===
com
org
io
kr
co.kr
*.ck
!www.ck
한국
// ===END ICANN DOMAINS===
// ===BEGIN PRIVATE DOMAINS===
github.io
// ===END PRIVATE DOMAINS===
"""

# (시간대, 기준 날짜, 가중치). New York 2026-03-08은 23시간, 2026-11-01은 25시간이다.
TIMEZONE_DAYS = (
    ("Asia/Seoul", date(2026, 11, 4), 4),
    ("UTC", date(2026, 11, 4), 2),
    ("America/New_York", date(2026, 11, 4), 2),
    ("America/New_York", date(2026, 3, 8), 1),
    ("America/New_York", date(2026, 11, 1), 1),
)
APPS = ("com.a.editor", "com.b.term", "com.tinyspeck.slackmacgap", "com.kakao.Talk")
CHROME = "com.google.Chrome"
BROWSERS = (CHROME, "com.apple.Safari", "org.mozilla.firefox")
# URL 분류와 가중치. D7(IP·localhost·점 없는 호스트·공개 접미사 자체·비 http), 대소문자·끝 점,
# IDN(D12: NFD·전각·끝 점 둘), scheme 없는 URL(D9).
URLS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        6,
        (
            "https://m.youtube.com/watch?v=1",
            "https://www.youtube.com/",
            "https://github.com/x",
            "https://gist.github.com/y",
            "https://foo.github.io/",
            "https://a.b.co.kr/",
            "https://www.ck/",
            "https://a.b.ck/",
        ),
    ),
    (1, ("http://192.168.0.1:8080/", "http://[::1]:3000/")),
    (1, ("http://localhost:8000/docs", "http://intranet/wiki")),
    (1, ("https://github.io/", "https://co.kr/")),
    (1, ("chrome://settings", "about:blank", "file:///Users/a.pdf")),
    (1, ("https://WWW.YouTube.COM./x", "https://GitHub.com./")),
    (
        1,
        (
            "https://예시.한국/",
            "https://xn--9n2bp8q.xn--3e0b707e/",
            "https://" + unicodedata.normalize("NFD", "예시.한국") + "/",
            "https://ｇｉｔｈｕｂ.com/",
            "https://github.com../",
        ),
    ),
    (
        1,
        (
            "meet.google.com/x",
            "github.com/explore",
            "localhost:8000/docs",
            "192.168.0.1:8080/",
            "about:blank",
            "mailto:a@b.com",
            "About:blank",
            "example.com/?r=https://a.com",
            "javascript:void(0)",
            "data:text/html,hi",
        ),
    ),
)
LABELS = ("L-coding", "L-leisure", "L-chat")
WILD_SECONDS = (0, 0, 1, 5, 59, 60, 61, 300, 600, 1800, 3600, 7200)
# 불투명 구간 문맥에 숨겨 둔 값. 어떤 출력에도 나오면 안 된다(E8).
OPAQUE_SECRET = "opaque-secret"


@dataclass(frozen=True, kw_only=True)
class GeneratedTimeline:
    """같은 타임라인의 참조 구현 입력과 제품 입력, 걸친 날짜(앞뒤 하루 포함)와 세션 id."""

    data: dict[str, Any]
    snapshot: SourceSnapshot
    days: list[str]
    session_ids: list[str]


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds")


def local_midnight(zone: ZoneInfo, day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)


class Draws:
    """`draw` 위의 작은 도구. 확률은 백분율 정수다."""

    def __init__(self, draw: st.DrawFn) -> None:
        self.draw = draw

    def pick[T](self, values: tuple[T, ...] | list[T]) -> T:
        return self.draw(st.sampled_from(values))

    def chance(self, percent: int) -> bool:
        return self.draw(st.integers(0, 99)) < percent

    def weighted[T](self, choices: tuple[tuple[int, T], ...]) -> T:
        return self.pick([value for weight, value in choices for _ in range(weight)])


@st.composite
def timelines(draw: st.DrawFn) -> GeneratedTimeline:
    pick = Draws(draw)
    timezone, day = pick.weighted(
        tuple((weight, (name, day)) for name, day, weight in TIMEZONE_DAYS)
    )
    zone = ZoneInfo(timezone)
    midnight = local_midnight(zone, day)
    next_midnight = local_midnight(zone, day + timedelta(days=1))
    origin = midnight - pick.pick((0, 1, 59, 600, 3600, 3 * 3600)) * SECOND
    if pick.chance(30):  # 시각이 밀리초·마이크로초 단위
        origin += timedelta(microseconds=pick.pick((1, 123000, 999999)))
    if pick.chance(25):  # 다음 자정에 닿는 입력
        origin = next_midnight - pick.pick((0, 1, 600, 3600)) * SECOND
    sessions = [
        focus_session(pick, origin=origin, number=number)
        for number in range(pick.pick((0, 1, 1, 2)))
    ]
    points = sorted(
        {midnight, next_midnight}
        | {
            datetime.fromisoformat(moment)
            for item in sessions
            for moment in [
                item["started_at"],
                item["ended_at"],
                *(value for pause in item["pauses"] or [] for value in pause),
            ]
        }
    )
    segments, confirmations = segments_from(pick, origin=origin, points=points)
    data = {
        "timezone": timezone,
        "segments": segments,
        "confirmations": confirmations,
        "sessions": sessions,
    }
    return GeneratedTimeline(
        data=data,
        snapshot=snapshot(data),
        days=covered_days(data),
        session_ids=[item["id"] for item in sessions],
    )


def focus_session(pick: Draws, *, origin: datetime, number: int) -> dict[str, Any]:
    started = origin + pick.pick((0, 60, 600, 1800, 3600, 7200)) * SECOND
    started += number * 4 * 3600 * SECOND
    ended = started + pick.pick((60, 600, 1800, 3600, 5400)) * SECOND
    mode = pick.draw(st.integers(0, 99))
    pauses: list[tuple[datetime, datetime]] | None = None
    if mode >= 25:
        pauses = [] if mode < 45 else pauses_of(pick, started=started, ended=ended)
    return {
        "id": f"f{number + 1}",
        "started_at": iso(started),
        "ended_at": iso(ended),
        "label_id": pick.pick(("L-coding", "L-coding", "L-leisure")),
        "pauses": None
        if pauses is None
        else [[iso(low), iso(high)] for low, high in pauses],
    }


def pauses_of(
    pick: Draws, *, started: datetime, ended: datetime
) -> list[tuple[datetime, datetime]]:
    pauses: list[tuple[datetime, datetime]] = []
    cursor = started
    for _ in range(pick.pick((1, 1, 2, 3))):
        low = cursor + pick.pick((0, 0, 60, 300, 600)) * SECOND
        high = low + pick.pick((1, 60, 300, 600)) * SECOND
        if high > ended or low >= ended:
            break
        pauses.append((low, high))
        cursor = high
    if pick.chance(20) and pauses and pauses[-1][1] < ended:  # 일시중지로 끝나는 세션
        pauses[-1] = (pauses[-1][0], ended)
    return pauses


def segments_from(
    pick: Draws, *, origin: datetime, points: list[datetime]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    segments: list[dict[str, Any]] = []
    confirmations: list[dict[str, Any]] = []
    moment = origin
    count = pick.draw(st.integers(1, 40))
    for index in range(count):
        upcoming = [point for point in points if point >= moment]
        if upcoming and pick.chance(35):  # 다음 경계 시각에 끝을 맞춘다(0초도 생긴다)
            target = upcoming[0] if pick.chance(70) else pick.pick(upcoming)
            end = target
        else:
            end = moment + pick.pick(WILD_SECONDS) * SECOND
        last = index == count - 1
        kind = pick.draw(st.integers(0, 99))
        if kind < 7:
            gap_end = None if last and pick.chance(40) else end
            segments.append(gap(index, start=moment, end=gap_end))
            moment = end
            continue
        segment = activity(pick, index, start=moment, end=end, opaque=kind < 13)
        if last and pick.chance(30):  # 열린 구간
            segment["ended_at"] = None
        segments.append(segment)
        if pick.chance(70):
            confirmations.append(confirmation(pick, segment))
        moment = end
        if not last and pick.chance(12):  # 60초 초과 침묵: 다음 구간은 늦게 시작한다
            moment = end + pick.pick((61, 120, 900)) * SECOND
    return segments, confirmations


def gap(index: int, *, start: datetime, end: datetime | None) -> dict[str, Any]:
    return {
        "id": f"s{index}",
        "segment_type": "capture_gap",
        "started_at": iso(start),
        "ended_at": None if end is None else iso(end),
        "first_event_id": f"e{index}",
    }


def activity(
    pick: Draws, index: int, *, start: datetime, end: datetime, opaque: bool
) -> dict[str, Any]:
    return {
        "id": f"s{index}",
        "segment_type": "activity",
        "started_at": iso(start),
        "ended_at": iso(end),
        "last_observed_at": iso(end),
        "first_event_id": f"e{index}",
        "version": pick.pick(("v1", "v1", "v2")),
        "context": opaque_context(pick, index) if opaque else context(pick),
    }


def opaque_context(pick: Draws, index: int) -> dict[str, Any]:
    """불투명 문맥. 절반은 숨겨야 할 앱·창·URL 값을 함께 담는다(E8)."""
    if not pick.chance(50):
        return {"kind": "opaque"}
    secret = f"{OPAQUE_SECRET}-{index}"
    return {
        "kind": "opaque",
        "app": {"bundleId": {"status": "captured", "value": f"{secret}.app"}},
        "window": {
            "status": "captured",
            "title": {"status": "captured", "value": secret},
        },
        "web": {
            "kind": "browser",
            "url": {"status": "captured", "value": f"https://{secret}.com/"},
        },
    }


def context(pick: Draws) -> dict[str, Any]:
    roll = pick.draw(st.integers(0, 99))
    if roll < 8:  # 앱 미확인
        return {
            "kind": "detailed",
            "app": {
                "name": {"status": "unavailable"},
                "bundleId": {"status": pick.pick(("absent", "unavailable"))},
            },
            "window": {"status": "unavailable"},
            "web": {"kind": "not_applicable"},
        }
    window = {
        "status": "captured",
        "title": {"status": "captured", "value": pick.pick(("t1", "t2"))},
    }
    if roll < 45:
        bundle_id = pick.pick(BROWSERS)
        web = {"kind": "browser", "url": browser_url(pick)}
    else:
        # 브라우저 bundleId인데 web.kind가 not_applicable인 구간도 만든다(Q3).
        bundle_id = pick.pick((*APPS, CHROME))
        web = {"kind": "not_applicable"}
    return {
        "kind": "detailed",
        "app": {
            "name": {"status": "captured", "value": bundle_id.split(".")[-1]},
            "bundleId": {"status": "captured", "value": bundle_id},
        },
        "window": window,
        "web": web,
    }


def browser_url(pick: Draws) -> dict[str, Any]:
    if pick.chance(20):
        return {"status": pick.pick(("redacted", "unavailable", "absent"))}
    return {"status": "captured", "value": pick.pick(pick.weighted(URLS))}


def confirmation(pick: Draws, segment: dict[str, Any]) -> dict[str, Any]:
    label = None if pick.chance(12) else pick.pick(LABELS)
    version = segment["version"]
    stale = "v0" if version != "v0" else "v1"
    return {
        "first_event_id": segment["first_event_id"],
        "segment_version": version if not pick.chance(12) else stale,
        "label_id": label,
    }


def covered_days(data: dict[str, Any]) -> list[str]:
    """구간·세션이 걸친 계정 시간대 날짜(앞뒤 하루 포함)."""
    zone = ZoneInfo(data["timezone"])
    moments = [
        datetime.fromisoformat(value)
        for item in data["segments"] + data["sessions"]
        for value in (
            item["started_at"],
            item.get("ended_at") or item.get("last_observed_at"),
        )
        if value
    ]
    first = min(moments).astimezone(zone).date() - timedelta(days=1)
    last = max(moments).astimezone(zone).date() + timedelta(days=1)
    return [
        (first + timedelta(days=offset)).isoformat()
        for offset in range((last - first).days + 1)
    ]
