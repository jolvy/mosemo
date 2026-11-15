"""MU-3: 검증 계획 MU-1의 변이 7개를 제품 함수에 적용하면 사례 검사(SC-7)가 고정 입력에서 실패한다.

변이는 `monkeypatch`로 제품 함수 하나를 바꾼다. 변이가 없으면 실패하는 사례가 없다는 것도 함께
확인해, 실패가 변이 때문임을 보인다.
"""

from collections.abc import Callable, Sequence
from datetime import datetime

import pytest

from mosemo.analytics.domain import day_reading, session_analysis
from mosemo.analytics.domain.day_reading import BackwardRead
from mosemo.analytics.domain.session_analysis import Verdict
from mosemo.analytics.domain.source import (
    CompletedFocusSession,
    DayWindow,
    Interval,
    SourceSegment,
    SourceSnapshot,
)
from tests.support import analytics_product
from tests.support.analytics_cases import failing_cases


def read_after_start(segment: SourceSegment, window: Interval) -> bool:
    """날짜 읽기 `>=` → `>`."""
    start, end = window
    return segment.started_at < end and (
        segment.observed_end is None or segment.observed_end > start
    )


def closed_window(self: DayWindow, moment: datetime) -> bool:
    """날짜 창 반개 `[시작, 끝)` → 닫힘 `[시작, 끝]`."""
    return self.start <= moment <= self.end


def pending_before_opaque(
    segment: SourceSegment, *, snapshot: SourceSnapshot, session: CompletedFocusSession
) -> Verdict:
    """판정 순서 불투명 → 판정 대기를 판정 대기 → 불투명으로."""
    confirmation = snapshot.confirmation(segment)
    if confirmation is None:
        return Verdict.PENDING
    if segment.is_opaque:
        return Verdict.OPAQUE
    if confirmation.label_id is None:
        return Verdict.UNCLASSIFIED
    if confirmation.label_id == session.label_id:
        return Verdict.FOCUS
    return Verdict.LAPSE


def no_backward_read(
    self: BackwardRead, page: Sequence[SourceSegment]
) -> list[SourceSegment]:
    """거슬러 읽기 제거."""
    return []


def piece_bounds_with(
    *, zero_inside: Callable[[datetime, Interval], bool]
) -> Callable[[SourceSegment, Interval], Interval | None]:
    """0초 구간을 조각으로 넣는 조건만 바꾼 `piece_bounds`."""

    def bounds(segment: SourceSegment, interval: Interval) -> Interval | None:
        if segment.observed_end is None:
            return None
        start = max(segment.started_at, interval[0])
        end = min(segment.observed_end, interval[1])
        if start < end:
            return start, end
        instant = segment.started_at == segment.observed_end
        if instant and zero_inside(segment.started_at, interval):
            return start, start
        return None

    return bounds


def inside_work_with_instants(intervals: Sequence[Interval], moment: datetime) -> bool:
    """세션 경계 순간 포함: 작업 구간 `(시작, 끝)` → `[시작, 끝]`."""
    return any(low <= moment <= high for low, high in intervals)


MUTANTS: dict[str, tuple[object, str, object]] = {
    "날짜 읽기 >= → >": (day_reading, "is_read", read_after_start),
    "날짜 창 반개 → 닫힘": (DayWindow, "contains", closed_window),
    "불투명 ↔ 판정 대기 순서": (session_analysis, "verdict", pending_before_opaque),
    "거슬러 읽기 제거": (BackwardRead, "chain", no_backward_read),
    "0초 이탈 제외": (
        session_analysis,
        "piece_bounds",
        piece_bounds_with(zero_inside=lambda moment, interval: False),
    ),
    "세션 경계 순간 포함": (session_analysis, "inside_work", inside_work_with_instants),
    "작업 구간 끝 0초 포함": (
        session_analysis,
        "piece_bounds",
        piece_bounds_with(
            zero_inside=lambda moment, interval: interval[0] <= moment <= interval[1]
        ),
    ),
}


def test_no_case_fails_without_a_mutant() -> None:
    assert failing_cases(analytics_product) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_fails_a_case(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    owner, attribute, mutant = MUTANTS[name]
    monkeypatch.setattr(owner, attribute, mutant)

    assert failing_cases(analytics_product) != []
