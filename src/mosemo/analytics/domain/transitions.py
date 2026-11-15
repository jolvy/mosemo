"""C2 usage target transitions and C7 label transitions over the whole segment list."""

from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from mosemo.analytics.domain.source import SourceSnapshot
from mosemo.analytics.domain.usage_targets import UsageTarget, UsageTargetResolver


@dataclass(frozen=True, kw_only=True)
class TargetTransition:
    """C2 row. `previous_dwell_us` is the adjoining same-target run before it (D2)."""

    at: datetime
    previous: UsageTarget
    next: UsageTarget
    previous_dwell_us: int


@dataclass(frozen=True, kw_only=True)
class LabelTransition:
    """C7 row."""

    at: datetime
    previous: str
    next: str


@dataclass(frozen=True, kw_only=True)
class IndexedTransition:
    """A C2 transition with the position of the segment it enters (C6 uses the order)."""

    index: int
    transition: TargetTransition


def target_transitions(
    snapshot: SourceSnapshot, resolver: UsageTargetResolver
) -> list[IndexedTransition]:
    """Every C2 transition: adjoining segments whose identified targets differ."""
    segments = snapshot.segments
    targets = [resolver.target(segment) for segment in segments]
    transitions: list[IndexedTransition] = []
    dwell_us = 0
    for index, segment in enumerate(segments):
        previous = targets[index - 1] if index else None
        current = targets[index]
        joined = index > 0 and segments[index - 1].adjoins(segment)
        if joined and previous and current and previous != current:
            transitions.append(
                IndexedTransition(
                    index=index,
                    transition=TargetTransition(
                        at=segment.started_at,
                        previous=previous,
                        next=current,
                        previous_dwell_us=dwell_us,
                    ),
                )
            )
        continues = joined and current is not None and previous == current
        dwell_us = (dwell_us if continues else 0) + segment.length_us
    return transitions


def label_transitions(snapshot: SourceSnapshot) -> list[LabelTransition]:
    """Every C7 transition: adjoining segments with different valid labels."""
    transitions: list[LabelTransition] = []
    for before, after in pairwise(snapshot.segments):
        previous, current = snapshot.label(before), snapshot.label(after)
        if before.adjoins(after) and previous and current and previous != current:
            transitions.append(
                LabelTransition(at=after.started_at, previous=previous, next=current)
            )
    return transitions
