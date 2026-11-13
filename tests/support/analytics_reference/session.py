"""세션 분석 C3~C6과 세션별 C7, 일시중지(FR-13·25·26)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .sites import PublicSuffixList
from .timeline import (
    Segment,
    Session,
    TargetTransition,
    Timeline,
    format_time,
    label_transitions,
    micros,
    parse,
    target_transitions,
)

Interval = tuple[datetime, datetime]

OPAQUE = "opaque"
PENDING = "pending"
UNCLASSIFIED = "unclassified"
FOCUS = "focus"
LAPSE = "lapse"


@dataclass(frozen=True, kw_only=True)
class Piece:
    """세션 분석 시간과 겹치는 활동 조각. 0초 구간은 0초 조각이다."""

    segment: Segment
    index: int  # 구간 순서상 위치
    start: datetime
    end: datetime
    verdict: str

    @property
    def length(self) -> timedelta:
        return self.end - self.start


@dataclass(kw_only=True)
class Lapse:
    """집중 이탈 1건."""

    first: Piece
    previous_focus: Piece | None
    pieces: list[Piece] = field(default_factory=list)
    end: datetime | None = None
    return_piece: Piece | None = None

    def inner_transitions(self, transitions: list[TargetTransition]) -> int:
        """FR-20: 진입 전환(첫 이탈 조각의 구간으로 들어온 전환)과 복귀 전환(복귀 집중
        조각의 구간으로 들어간 전환) 사이의 전환. 시각이 아니라 구간 순서로 가른다."""
        last = self.return_piece.index if self.return_piece else None
        return sum(
            1
            for transition in transitions
            if self.first.index < transition.index
            and (last is None or transition.index < last)
        )


def work_intervals(session: Session) -> list[Interval]:
    """세션 분석 시간을 이루는 작업 구간. 일시중지 정보가 없으면 시작~종료 전체다."""
    cursor = session.started_at
    intervals: list[Interval] = []
    for pause_start, pause_end in session.pauses or []:
        if pause_start > cursor:
            intervals.append((cursor, pause_start))
        cursor = max(cursor, pause_end)
    if session.ended_at > cursor:
        intervals.append((cursor, session.ended_at))
    return intervals


def verdict(timeline: Timeline, segment: Segment, session: Session) -> str:
    """FR-14 순서: 불투명 → 판정 대기 → 확정 미분류 → 집중 → 이탈."""
    if segment.is_opaque:
        return OPAQUE
    confirmation = timeline.confirmation(segment)
    if confirmation is None:
        return PENDING
    if confirmation.label_id is None:
        return UNCLASSIFIED
    return FOCUS if confirmation.label_id == session.label_id else LAPSE


def pieces_of(
    timeline: Timeline, session: Session, intervals: list[Interval]
) -> list[Piece]:
    pieces = []
    for index, segment in enumerate(timeline.segments):
        if not segment.is_activity or segment.observed_end is None:
            continue
        for low, high in intervals:
            start = max(segment.started_at, low)
            end = min(segment.observed_end, high)
            instant = segment.started_at == segment.observed_end
            if start < end or (instant and low <= segment.started_at < high):
                pieces.append(
                    Piece(
                        segment=segment,
                        index=index,
                        start=start,
                        end=max(start, end),
                        verdict=verdict(timeline, segment, session),
                    )
                )
    return pieces


def lapses_of(pieces: list[Piece], analysis_end: datetime | None) -> list[Lapse]:
    """FR-16: 집중 조각으로 돌아오거나 세션 분석 시간이 끝날 때까지(D8)."""
    lapses: list[Lapse] = []
    current: Lapse | None = None
    last_focus: Piece | None = None
    for piece in pieces:
        if piece.verdict == LAPSE:
            if current is None:
                current = Lapse(first=piece, previous_focus=last_focus)
                lapses.append(current)
            current.pieces.append(piece)
        elif piece.verdict == FOCUS:
            if current is not None:
                current.end, current.return_piece = piece.start, piece
                current = None
            last_focus = piece
    if current is not None:
        current.end = analysis_end
    return lapses


def elapsed(intervals: list[Interval], moment: datetime) -> timedelta:
    """세션 시작부터 moment까지 지난 세션 분석 시간(일시중지 제외)."""
    return sum(
        (min(high, moment) - low for low, high in intervals if low < moment),
        timedelta(),
    )


def inside_work(intervals: list[Interval], moment: datetime) -> bool:
    """작업 구간의 시작·끝 순간을 뺀 열린 구간 안에 있다(FR-12)."""
    return any(low < moment < high for low, high in intervals)


def composition(
    pieces: list[Piece], analysis: timedelta, lapse_count: int
) -> dict[str, int]:
    totals = {
        name: micros(sum((p.length for p in pieces if p.verdict == name), timedelta()))
        for name in (FOCUS, LAPSE, UNCLASSIFIED, PENDING, OPAQUE)
    }
    return {
        "focus_us": totals[FOCUS],
        "lapse_us": totals[LAPSE],
        "unclassified_us": totals[UNCLASSIFIED],
        "pending_us": totals[PENDING],
        "opaque_us": totals[OPAQUE],
        "unobserved_us": micros(analysis) - sum(totals.values()),
        "lapse_count": lapse_count,
    }


def distractions(pieces: list[Piece], sites: PublicSuffixList) -> list[dict[str, Any]]:
    """C5. 직전 조각은 불투명을 건너뛴 바로 앞 조각이고, 이탈을 시작한 조각은 진입이다."""
    rows: dict[str, list[int]] = {}
    previous: str | None = None
    lapsing = False
    for piece in pieces:
        if piece.verdict == OPAQUE:
            continue
        key = piece.segment.key(sites)
        if piece.verdict == LAPSE:
            row = rows.setdefault(key, [0, 0])
            row[0] += 1 if not lapsing or key != previous else 0
            row[1] += micros(piece.length)
            lapsing = True
        elif piece.verdict == FOCUS:
            lapsing = False
        previous = key
    return [
        {"key": key, "entries": entries, "lapse_us": us}
        for key, (entries, us) in sorted(
            rows.items(), key=lambda item: (-item[1][1], item[0])
        )
    ]


def session(data: Mapping[str, Any], session_id: str, psl: str) -> dict[str, Any]:
    """그 세션의 C3~C6과 세션별 C7."""
    timeline = parse(data)
    sites = PublicSuffixList(psl)
    focus = timeline.sessions[session_id]
    intervals = work_intervals(focus)
    analysis = sum((high - low for low, high in intervals), timedelta())
    pieces = pieces_of(timeline, focus, intervals)
    lapses = lapses_of(pieces, intervals[-1][1] if intervals else None)
    c3 = [
        transition
        for transition in target_transitions(timeline.segments, sites)
        if inside_work(intervals, transition.at)
    ]
    return {
        "has_pause_info": focus.pauses is not None,
        "analysis_us": micros(analysis),
        "c3": [transition.row() for transition in c3],
        "c4": composition(pieces, analysis, len(lapses)),
        "c5": distractions(pieces, sites),
        "c6": [
            {
                "start": format_time(lapse.first.start),
                "end": format_time(lapse.end) if lapse.end else None,
                "entry_segment_id": lapse.first.segment.id,
                "entry_target": lapse.first.segment.key(sites),
                "entry_label_id": verdict_label(timeline, lapse.first.segment),
                "entry_context": lapse.first.segment.context,
                "prev_focus_target": lapse.previous_focus.segment.key(sites)
                if lapse.previous_focus
                else None,
                "elapsed_us": micros(elapsed(intervals, lapse.first.start)),
                "lapse_us": micros(
                    sum((piece.length for piece in lapse.pieces), timedelta())
                ),
                "transitions": lapse.inner_transitions(c3),
                "returned": lapse.return_piece is not None,
            }
            for lapse in lapses
        ],
        "c7": [
            transition.row()
            for transition in label_transitions(timeline, timeline.segments)
            if inside_work(intervals, transition.at)
        ],
    }


def verdict_label(timeline: Timeline, segment: Segment) -> str | None:
    confirmation = timeline.confirmation(segment)
    return confirmation.label_id if confirmation else None
