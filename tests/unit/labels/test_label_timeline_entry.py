from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from mosemo.activity_labels.confirmations.models import ActivityLabelConfirmation
from mosemo.activity_labels.queries.service import LabelTimelineEntry
from mosemo.observation.public import ActivityTimelineKind, ObservedSegment

START = datetime(2026, 9, 14, tzinfo=UTC)
CODING_ID = uuid4()
LEARNING_ID = uuid4()
PENDING = object()


def make_confirmation(
    *, segment_version: str = "version", label_id: UUID | None = None
) -> ActivityLabelConfirmation:
    return ActivityLabelConfirmation(
        account_id=uuid4(),
        first_event_id=uuid4(),
        segment_version=segment_version,
        label_id=label_id,
        confirmed_at=START,
        updated_at=START,
    )


def make_entry(
    *, started_at: datetime, ended_at: datetime, label: object
) -> LabelTimelineEntry:
    return LabelTimelineEntry(
        segment=ObservedSegment(
            id=uuid4(),
            account_id=uuid4(),
            first_event_id=uuid4(),
            started_at=started_at,
            last_observed_at=ended_at,
            reason=None,
            context={"kind": "detailed"},
            ended_at=ended_at,
            kind=ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY,
            version="version",
        ),
        version="version",
        ended_at=ended_at,
        confirmation=(
            None
            if label is PENDING
            else make_confirmation(label_id=label if isinstance(label, UUID) else None)
        ),
        display_name=None,
    )


def test_applies_to_is_true_for_same_segment_version() -> None:
    assert make_confirmation(segment_version="v1").applies_to("v1")


def test_applies_to_is_false_for_other_segment_version() -> None:
    assert not make_confirmation(segment_version="v1").applies_to("v2")


@pytest.mark.parametrize(
    ("previous_label", "label", "gap", "expected"),
    [
        (CODING_ID, CODING_ID, timedelta(seconds=1), False),
        (PENDING, None, timedelta(0), False),
        (None, PENDING, timedelta(0), False),
        (CODING_ID, CODING_ID, timedelta(0), True),
        (None, None, timedelta(0), True),
        (PENDING, PENDING, timedelta(0), True),
        (CODING_ID, LEARNING_ID, timedelta(0), False),
        (CODING_ID, None, timedelta(0), False),
    ],
    ids=[
        "time-gap",
        "pending-then-unclassified",
        "unclassified-then-pending",
        "same-label",
        "both-unclassified",
        "both-pending",
        "different-labels",
        "label-then-unclassified",
    ],
)
def test_joins(
    previous_label: object, label: object, gap: timedelta, expected: bool
) -> None:
    previous = make_entry(
        started_at=START,
        ended_at=START + timedelta(minutes=1),
        label=previous_label,
    )
    entry = make_entry(
        started_at=previous.ended_at + gap,
        ended_at=previous.ended_at + gap + timedelta(minutes=1),
        label=label,
    )

    assert entry.joins(previous) is expected
