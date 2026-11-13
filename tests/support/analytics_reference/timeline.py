"""입력 해석, 사용 대상(FR-1~3, D7), 맞닿음과 C2·C7 전환."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

from .sites import PublicSuffixList, web_host

UNKNOWN_APP = "앱 미확인"
MICROSECOND = timedelta(microseconds=1)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"시각에 오프셋이 없다: {value}")
    return parsed.astimezone(UTC)


def format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def micros(delta: timedelta) -> int:
    return delta // MICROSECOND


@dataclass(frozen=True, kw_only=True)
class Segment:
    id: str
    is_activity: bool
    started_at: datetime
    ended_at: datetime | None
    # 활동은 COALESCE(ended_at, last_observed_at)(FR-6), 공백은 ended_at(열리면 None).
    observed_end: datetime | None
    first_event_id: str
    version: str | None
    context: Mapping[str, Any] | None

    @property
    def is_opaque(self) -> bool:
        return self.is_activity and (self.context or {}).get("kind") == "opaque"

    @property
    def app(self) -> str | None:
        """상세 문맥 활동의 captured bundleId. 없으면 None(앱 미확인)."""
        if not self.is_activity or self.is_opaque:
            return None
        bundle_id = (self.context or {}).get("app", {}).get("bundleId", {})
        if bundle_id.get("status") != "captured":
            return None
        return bundle_id["value"]

    @property
    def host(self) -> str | None:
        """브라우저 구간의 captured `http`·`https` URL 호스트. 사이트 후보다."""
        if self.app is None:
            return None
        web = (self.context or {}).get("web", {})
        url = web.get("url", {})
        if web.get("kind") != "browser" or url.get("status") != "captured":
            return None
        return web_host(url["value"])

    @property
    def is_unknown_app(self) -> bool:
        return self.is_activity and not self.is_opaque and self.app is None

    def target(self, sites: PublicSuffixList) -> str | None:
        """사용 대상. 공백·불투명·앱 미확인은 None."""
        host = self.host
        if host is not None:
            return sites.site(host)
        return self.app

    def key(self, sites: PublicSuffixList) -> str:
        """출력용 사용 대상. 앱 미확인은 `앱 미확인`이다(불투명에는 쓰지 않는다)."""
        return self.target(sites) or UNKNOWN_APP

    @property
    def length(self) -> timedelta:
        end = self.observed_end or self.started_at
        return end - self.started_at


@dataclass(frozen=True, kw_only=True)
class Confirmation:
    label_id: str | None


@dataclass(frozen=True, kw_only=True)
class Session:
    id: str
    started_at: datetime
    ended_at: datetime
    label_id: str
    pauses: list[tuple[datetime, datetime]] | None


@dataclass(frozen=True, kw_only=True)
class Timeline:
    timezone: str
    segments: list[Segment]
    confirmations: dict[str, tuple[str, str | None]]
    sessions: dict[str, Session]

    def confirmation(self, segment: Segment) -> Confirmation | None:
        """유효한 확정. 확정 당시 version이 현재 구간 version과 같을 때만 있다."""
        if not segment.is_activity:
            return None
        stored = self.confirmations.get(segment.first_event_id)
        if stored is None or stored[0] != segment.version:
            return None
        return Confirmation(label_id=stored[1])

    def label(self, segment: Segment) -> str | None:
        """C7에 쓰는 라벨. 불투명·판정 대기·확정 미분류는 None."""
        if segment.is_opaque:
            return None
        confirmation = self.confirmation(segment)
        return confirmation.label_id if confirmation else None


def parse(data: Mapping[str, Any]) -> Timeline:
    return Timeline(
        timezone=data["timezone"],
        segments=[parse_segment(item) for item in data.get("segments", [])],
        confirmations={
            item["first_event_id"]: (item["segment_version"], item["label_id"])
            for item in data.get("confirmations", [])
        },
        sessions={item["id"]: parse_session(item) for item in data.get("sessions", [])},
    )


def parse_segment(item: Mapping[str, Any]) -> Segment:
    is_activity = item["segment_type"] == "activity"
    ended_at = optional_time(item.get("ended_at"))
    observed_end = ended_at
    if is_activity and ended_at is None:
        observed_end = parse_time(item["last_observed_at"])
    return Segment(
        id=item["id"],
        is_activity=is_activity,
        started_at=parse_time(item["started_at"]),
        ended_at=ended_at,
        observed_end=observed_end,
        first_event_id=item["first_event_id"],
        version=item.get("version") if is_activity else None,
        context=item.get("context") if is_activity else None,
    )


def parse_session(item: Mapping[str, Any]) -> Session:
    pauses = item.get("pauses")
    return Session(
        id=item["id"],
        started_at=parse_time(item["started_at"]),
        ended_at=parse_time(item["ended_at"]),
        label_id=item["label_id"],
        pauses=None
        if pauses is None
        else [(parse_time(start), parse_time(end)) for start, end in pauses],
    )


def optional_time(value: str | None) -> datetime | None:
    return None if value is None else parse_time(value)


def adjacent(before: Segment, after: Segment) -> bool:
    """구간 순서상 이웃이고 앞 구간의 ended_at이 뒤 구간의 started_at과 같다."""
    return before.ended_at is not None and before.ended_at == after.started_at


@dataclass(frozen=True, kw_only=True)
class TargetTransition:
    index: int  # 뒤 구간의 구간 순서상 위치
    at: datetime
    previous: str
    next: str
    dwell: timedelta

    def row(self) -> dict[str, Any]:
        return {
            "at": format_time(self.at),
            "prev": self.previous,
            "next": self.next,
            "dwell_us": micros(self.dwell),
        }


@dataclass(frozen=True, kw_only=True)
class LabelTransition:
    at: datetime
    previous: str
    next: str

    def row(self) -> dict[str, Any]:
        return {"at": format_time(self.at), "prev": self.previous, "next": self.next}


def target_transitions(
    segments: Sequence[Segment], sites: PublicSuffixList
) -> list[TargetTransition]:
    """C2 전환 전체. 체류 시간은 같은 사용 대상으로 맞닿아 이어진 구간 길이의 합(D2)."""
    targets = [segment.target(sites) for segment in segments]
    transitions: list[TargetTransition] = []
    dwell = timedelta()
    for index, segment in enumerate(segments):
        previous = targets[index - 1] if index else None
        current = targets[index]
        joined = index > 0 and adjacent(segments[index - 1], segment)
        if joined and previous and current and previous != current:
            transitions.append(
                TargetTransition(
                    index=index,
                    at=segment.started_at,
                    previous=previous,
                    next=current,
                    dwell=dwell,
                )
            )
        continues = joined and current is not None and previous == current
        dwell = (dwell if continues else timedelta()) + segment.length
    return transitions


def label_transitions(
    timeline: Timeline, segments: Sequence[Segment]
) -> list[LabelTransition]:
    """C7 전환 전체. 맞닿은 두 구간이 모두 유효한 확정 라벨(미분류 제외)이고 다르면 1회."""
    transitions: list[LabelTransition] = []
    for before, after in pairwise(segments):
        previous, current = timeline.label(before), timeline.label(after)
        if adjacent(before, after) and previous and current and previous != current:
            transitions.append(
                LabelTransition(at=after.started_at, previous=previous, next=current)
            )
    return transitions
