"""Session analysis C3~C6 and per-session C7 (FR-12~FR-20, D3, D4, D8, D10, D11)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from mosemo.analytics.domain.source import (
    CompletedFocusSession,
    Interval,
    SourceSegment,
    SourceSnapshot,
    micros,
)
from mosemo.analytics.domain.transitions import (
    IndexedTransition,
    LabelTransition,
    TargetTransition,
    label_transitions,
    target_transitions,
)
from mosemo.analytics.domain.usage_targets import LapseTarget, UsageTargetResolver


class Verdict(StrEnum):
    OPAQUE = "opaque"
    PENDING = "pending"
    UNCLASSIFIED = "unclassified"
    FOCUS = "focus"
    LAPSE = "lapse"


def verdict(
    segment: SourceSegment, *, snapshot: SourceSnapshot, session: CompletedFocusSession
) -> Verdict:
    """FR-14 order: opaque → pending → unclassified → focus → lapse."""
    if segment.is_opaque:
        return Verdict.OPAQUE
    confirmation = snapshot.confirmation(segment)
    if confirmation is None:
        return Verdict.PENDING
    if confirmation.label_id is None:
        return Verdict.UNCLASSIFIED
    if confirmation.label_id == session.label_id:
        return Verdict.FOCUS
    return Verdict.LAPSE


def piece_bounds(segment: SourceSegment, interval: Interval) -> Interval | None:
    """The part of an activity inside a work interval `[low, high)` (D10).

    A zero-length segment is a zero-length piece only when its instant is inside.
    """
    if segment.observed_end is None:
        return None
    low, high = interval
    start = max(segment.started_at, low)
    end = min(segment.observed_end, high)
    if start < end:
        return start, end
    instant = segment.started_at == segment.observed_end
    if instant and low <= segment.started_at < high:
        return start, start
    return None


def inside_work(intervals: Sequence[Interval], moment: datetime) -> bool:
    """Inside a work interval without its start and end instants (FR-12)."""
    return any(low < moment < high for low, high in intervals)


@dataclass(frozen=True, kw_only=True)
class Piece:
    """An activity piece inside the session analysis time."""

    segment: SourceSegment
    index: int
    start: datetime
    end: datetime
    verdict: Verdict

    @property
    def length_us(self) -> int:
        return micros(self.end - self.start)


@dataclass(kw_only=True)
class Lapse:
    """One focus lapse while it is being collected."""

    first: Piece
    previous_focus: Piece | None
    pieces: list[Piece] = field(default_factory=list)
    end: datetime | None = None
    return_piece: Piece | None = None

    def inner_transitions(self, transitions: Sequence[IndexedTransition]) -> int:
        """C6: C3 transitions between the entry and the return in segment order."""
        last = self.return_piece.index if self.return_piece else None
        return sum(
            1
            for item in transitions
            if self.first.index < item.index and (last is None or item.index < last)
        )


@dataclass(frozen=True, kw_only=True)
class TimeComposition:
    """C4: the six times sum to the session analysis time; plus the lapse count."""

    focus_us: int = 0
    lapse_us: int = 0
    unclassified_us: int = 0
    pending_us: int = 0
    opaque_us: int = 0
    unobserved_us: int = 0
    lapse_count: int = 0

    def plus(self, other: TimeComposition) -> TimeComposition:
        return TimeComposition(
            focus_us=self.focus_us + other.focus_us,
            lapse_us=self.lapse_us + other.lapse_us,
            unclassified_us=self.unclassified_us + other.unclassified_us,
            pending_us=self.pending_us + other.pending_us,
            opaque_us=self.opaque_us + other.opaque_us,
            unobserved_us=self.unobserved_us + other.unobserved_us,
            lapse_count=self.lapse_count + other.lapse_count,
        )


@dataclass(frozen=True, kw_only=True)
class Distraction:
    """C5 row: entries into a target during lapses and the lapse time spent there."""

    target: LapseTarget
    entries: int
    lapse_us: int


def ranked_distractions(totals: Mapping[LapseTarget, list[int]]) -> list[Distraction]:
    """FR-4 order: lapse time descending, then key ascending."""
    rows = [
        Distraction(target=target, entries=entries, lapse_us=lapse_us)
        for target, (entries, lapse_us) in totals.items()
    ]
    return sorted(rows, key=lambda row: (-row.lapse_us, row.target.key))


@dataclass(frozen=True, kw_only=True)
class LapseEvidence:
    """C6 row for one focus lapse."""

    start: datetime
    end: datetime | None
    entry_segment_id: str
    entry_target: LapseTarget
    entry_label_id: str | None
    entry_context: Mapping[str, object] | None
    previous_focus_target: LapseTarget | None
    elapsed_us: int
    lapse_us: int
    transitions: int
    returned: bool


@dataclass(frozen=True, kw_only=True)
class SessionAnalysis:
    has_pause_info: bool
    analysis_us: int
    target_transitions: tuple[TargetTransition, ...]
    composition: TimeComposition
    distractions: tuple[Distraction, ...]
    lapses: tuple[LapseEvidence, ...]
    label_transitions: tuple[LabelTransition, ...]


@dataclass(frozen=True, kw_only=True)
class SessionMeasure:
    """Work intervals, pieces and lapses of one session (shared with daily totals)."""

    intervals: tuple[Interval, ...]
    analysis_us: int
    pieces: tuple[Piece, ...]
    lapses: tuple[Lapse, ...]

    def composition(self) -> TimeComposition:
        totals = {
            name: sum(piece.length_us for piece in self.pieces if piece.verdict is name)
            for name in Verdict
        }
        return TimeComposition(
            focus_us=totals[Verdict.FOCUS],
            lapse_us=totals[Verdict.LAPSE],
            unclassified_us=totals[Verdict.UNCLASSIFIED],
            pending_us=totals[Verdict.PENDING],
            opaque_us=totals[Verdict.OPAQUE],
            unobserved_us=self.analysis_us - sum(totals.values()),
            lapse_count=len(self.lapses),
        )

    def distraction_totals(
        self, resolver: UsageTargetResolver
    ) -> dict[LapseTarget, list[int]]:
        """C5 (D4, D11): the previous piece skips opaque pieces; a lapse start enters."""
        totals: dict[LapseTarget, list[int]] = {}
        previous: LapseTarget | None = None
        lapsing = False
        for piece in self.pieces:
            if piece.verdict is Verdict.OPAQUE:
                continue
            target = resolver.lapse_target(piece.segment)
            if piece.verdict is Verdict.LAPSE:
                row = totals.setdefault(target, [0, 0])
                row[0] += 1 if not lapsing or target != previous else 0
                row[1] += piece.length_us
                lapsing = True
            elif piece.verdict is Verdict.FOCUS:
                lapsing = False
            previous = target
        return totals


def measure(snapshot: SourceSnapshot, session: CompletedFocusSession) -> SessionMeasure:
    intervals = session.work_intervals()
    pieces = tuple(session_pieces(snapshot, session, intervals=intervals))
    return SessionMeasure(
        intervals=intervals,
        analysis_us=sum(micros(high - low) for low, high in intervals),
        pieces=pieces,
        lapses=tuple(lapses_of(pieces, intervals[-1][1] if intervals else None)),
    )


def session_pieces(
    snapshot: SourceSnapshot,
    session: CompletedFocusSession,
    *,
    intervals: Sequence[Interval],
) -> list[Piece]:
    pieces: list[Piece] = []
    for index, segment in enumerate(snapshot.segments):
        if not segment.is_activity:
            continue
        for interval in intervals:
            bounds = piece_bounds(segment, interval)
            if bounds is not None:
                pieces.append(
                    Piece(
                        segment=segment,
                        index=index,
                        start=bounds[0],
                        end=bounds[1],
                        verdict=verdict(segment, snapshot=snapshot, session=session),
                    )
                )
    return pieces


def lapses_of(pieces: Sequence[Piece], analysis_end: datetime | None) -> list[Lapse]:
    """FR-16: a lapse lasts until a focus piece or the end of the analysis time (D8)."""
    lapses: list[Lapse] = []
    current: Lapse | None = None
    last_focus: Piece | None = None
    for piece in pieces:
        if piece.verdict is Verdict.LAPSE:
            if current is None:
                current = Lapse(first=piece, previous_focus=last_focus)
                lapses.append(current)
            current.pieces.append(piece)
        elif piece.verdict is Verdict.FOCUS:
            if current is not None:
                current.end, current.return_piece = piece.start, piece
                current = None
            last_focus = piece
    if current is not None:
        current.end = analysis_end
    return lapses


def elapsed_us(intervals: Sequence[Interval], moment: datetime) -> int:
    """Session analysis time from the session start to `moment` (pauses excluded)."""
    return sum(
        micros(min(high, moment) - low) for low, high in intervals if low < moment
    )


class SessionAnalyzer:
    """Analyses one completed focus session from a source snapshot."""

    def __init__(self, resolver: UsageTargetResolver) -> None:
        self._resolver = resolver

    def analyze(
        self, snapshot: SourceSnapshot, session: CompletedFocusSession
    ) -> SessionAnalysis:
        measured = measure(snapshot, session)
        c3 = [
            item
            for item in target_transitions(snapshot, self._resolver)
            if inside_work(measured.intervals, item.transition.at)
        ]
        return SessionAnalysis(
            has_pause_info=session.has_pause_info,
            analysis_us=measured.analysis_us,
            target_transitions=tuple(item.transition for item in c3),
            composition=measured.composition(),
            distractions=tuple(
                ranked_distractions(measured.distraction_totals(self._resolver))
            ),
            lapses=tuple(
                self._evidence(lapse, snapshot=snapshot, measured=measured, c3=c3)
                for lapse in measured.lapses
            ),
            label_transitions=tuple(
                transition
                for transition in label_transitions(snapshot)
                if inside_work(measured.intervals, transition.at)
            ),
        )

    def _evidence(
        self,
        lapse: Lapse,
        *,
        snapshot: SourceSnapshot,
        measured: SessionMeasure,
        c3: Sequence[IndexedTransition],
    ) -> LapseEvidence:
        entry = lapse.first.segment
        confirmation = snapshot.confirmation(entry)
        previous = lapse.previous_focus
        return LapseEvidence(
            start=lapse.first.start,
            end=lapse.end,
            entry_segment_id=entry.id,
            entry_target=self._resolver.lapse_target(entry),
            entry_label_id=confirmation.label_id if confirmation else None,
            entry_context=entry.context,
            previous_focus_target=self._resolver.lapse_target(previous.segment)
            if previous
            else None,
            elapsed_us=elapsed_us(measured.intervals, lapse.first.start),
            lapse_us=sum(piece.length_us for piece in lapse.pieces),
            transitions=lapse.inner_transitions(c3),
            returned=lapse.return_piece is not None,
        )
