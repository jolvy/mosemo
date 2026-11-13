"""활동 분석 참조 구현 테스트의 입력 작성 도구.

시각은 2026-11-03 00:00 Asia/Seoul부터의 분이다. 세션 예시는 `[0, 60)`, 세션 라벨 `코딩`이다.
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

BASE = datetime(2026, 11, 3, tzinfo=ZoneInfo("Asia/Seoul"))
DAY = "2026-11-03"
NEXT_DAY = "2026-11-04"
SEC = 1_000_000
MIN = 60 * SEC
CODING = "L-coding"
LEISURE = "L-leisure"
CHROME = "com.google.Chrome"
SAFARI = "com.apple.Safari"
SESSION = "f1"

PSL = """// 최소 Public Suffix List
// ===BEGIN ICANN DOMAINS===
com
io
kr
한국
// ===END ICANN DOMAINS===
// ===BEGIN PRIVATE DOMAINS===
github.io
// ===END PRIVATE DOMAINS===
"""

Time = float | str


def at(minutes: Time, seconds: int = 0) -> str:
    if isinstance(minutes, str):
        return minutes
    return (BASE + timedelta(minutes=minutes, seconds=seconds)).isoformat()


def utc(minutes: Time, seconds: int = 0) -> str:
    moment = datetime.fromisoformat(at(minutes, seconds)).astimezone(UTC)
    return moment.isoformat(timespec="microseconds")


def app(bundle_id: str, title: str = "창") -> dict[str, Any]:
    return {
        "kind": "detailed",
        "app": {
            "name": {"status": "captured", "value": bundle_id},
            "bundleId": {"status": "captured", "value": bundle_id},
        },
        "window": {
            "status": "captured",
            "title": {"status": "captured", "value": title},
        },
        "web": {"kind": "not_applicable"},
    }


def browser(url: str | None, bundle_id: str = CHROME) -> dict[str, Any]:
    context = app(bundle_id, title=url or "탭")
    context["web"] = {
        "kind": "browser",
        "url": {"status": "captured", "value": url}
        if url is not None
        else {"status": "absent"},
    }
    return context


def unknown_app(name: str = "?") -> dict[str, Any]:
    return {
        "kind": "detailed",
        "app": {
            "name": {"status": "captured", "value": name},
            "bundleId": {"status": "absent"},
        },
        "window": {"status": "absent"},
        "web": {"kind": "not_applicable"},
    }


OPAQUE: dict[str, Any] = {"kind": "opaque"}


def activity(
    sid: str,
    start: Time,
    end: Time | None,
    context: Mapping[str, Any],
    *,
    last: Time | None = None,
    version: str = "v1",
) -> dict[str, Any]:
    """활동 구간. `end`가 None이면 열린 구간이고 `last`가 마지막 관찰이다."""
    observed = last if last is not None else end
    if observed is None:
        raise ValueError("열린 구간에는 last가 필요하다")
    return {
        "id": sid,
        "segment_type": "activity",
        "started_at": at(start),
        "ended_at": None if end is None else at(end),
        "last_observed_at": at(observed),
        "first_event_id": f"e-{sid}",
        "version": version,
        "context": dict(context),
    }


def gap(sid: str, start: Time, end: Time | None) -> dict[str, Any]:
    return {
        "id": sid,
        "segment_type": "capture_gap",
        "started_at": at(start),
        "ended_at": None if end is None else at(end),
        "first_event_id": f"e-{sid}",
    }


def focus(
    *,
    start: Time = 0,
    end: Time = 60,
    label: str = CODING,
    pauses: list[tuple[Time, Time]] | None = None,
    sid: str = SESSION,
) -> dict[str, Any]:
    """완료된 집중 세션. `pauses=None`이면 일시중지 정보가 없는 세션이다."""
    return {
        "id": sid,
        "started_at": at(start),
        "ended_at": at(end),
        "label_id": label,
        "pauses": None
        if pauses is None
        else [[at(low), at(high)] for low, high in pauses],
    }


def timeline(
    *segments: dict[str, Any],
    labels: Mapping[str, str | None] | None = None,
    stale: Mapping[str, str | None] | None = None,
    sessions: list[dict[str, Any]] | None = None,
    timezone: str = "Asia/Seoul",
) -> dict[str, Any]:
    """입력 data. `labels`는 유효한 확정, `stale`은 version이 바뀐 확정(구간 id → 라벨)."""
    versions = {segment["id"]: segment.get("version") for segment in segments}
    confirmations = [
        {
            "first_event_id": f"e-{sid}",
            "segment_version": versions[sid],
            "label_id": label,
        }
        for sid, label in (labels or {}).items()
    ] + [
        {"first_event_id": f"e-{sid}", "segment_version": "v0", "label_id": label}
        for sid, label in (stale or {}).items()
    ]
    return {
        "timezone": timezone,
        "segments": list(segments),
        "confirmations": confirmations,
        "sessions": sessions or [],
    }


def only(data: Mapping[str, Any], ids: list[str]) -> dict[str, Any]:
    """`read_day`가 고른 구간만 남긴 data."""
    chosen = set(ids)
    return {
        **data,
        "segments": [item for item in data["segments"] if item["id"] in chosen],
    }


def resolve(value: Any, path: str) -> Any:
    """출력 경로. 점으로 나누고 숫자는 목록 위치, `#`은 길이, `*`는 목록 전체다."""
    return walk(value, path.split(".") if path else [])


def walk(value: Any, parts: list[str]) -> Any:
    if not parts:
        return value
    head, *rest = parts
    if head == "#":
        return len(value)
    if head == "*":
        return [walk(item, rest) for item in value]
    if isinstance(value, list):
        return walk(value[int(head)], rest)
    return walk(value[head], rest)
