"""Projected segments of one account never overlap (metrics D1 input contract)."""

import random
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from uuid import UUID

import pytest

from mosemo.observation.domain.activities import ActivityRecord, RecordType
from mosemo.observation.domain.timeline import (
    ProjectedActivity,
    ProjectedGap,
    ProjectedSegment,
    observation_key,
    project_events,
)
from mosemo.shared.domain.timezones import Timezone

START = datetime(2026, 9, 14, tzinfo=UTC)
CONTEXTS: list[dict[str, object]] = [
    {"kind": "opaque"},
    {"kind": "detailed", "app": {"name": "A"}},
    {"kind": "detailed", "app": {"name": "B"}},
]


def random_records(rng: random.Random, devices: list[UUID]) -> list[ActivityRecord]:
    records: list[ActivityRecord] = []
    observed_at = START
    sequences = dict.fromkeys(devices, 0)
    for _ in range(rng.randint(1, 80)):
        # Zero gaps make ties and zero-second segments; long gaps close activity.
        observed_at += timedelta(seconds=rng.choice([0, 0, 1, 5, 30, 60, 61, 300]))
        device = rng.choice(devices)
        sequences[device] += 1
        payload: dict[str, object]
        if rng.random() < 0.2:
            record_type = RecordType.COLLECTION_STATE_CHANGED
            payload = {
                "state": rng.choice(["suspended", "active"]),
                "reason": "user_paused",
            }
        else:
            record_type = RecordType.ACTIVITY_OBSERVATION
            payload = {"context": rng.choice(CONTEXTS)}
        records.append(
            ActivityRecord(
                event_id=UUID(int=rng.getrandbits(128)),
                device_id=device,
                sequence=sequences[device],
                record_type=record_type,
                observed_at=observed_at,
                timezone_id=Timezone.ASIA_SEOUL,
                utc_offset_minutes=540,
                payload=payload,
            )
        )
    return sorted(records, key=observation_key)


def span(segment: ProjectedSegment) -> tuple[datetime, datetime | None]:
    """`[started_at, end]`; the end is `None` only for an open capture gap."""
    if segment.ended_at is not None:
        return segment.started_at, segment.ended_at
    if isinstance(segment, ProjectedActivity):
        return segment.started_at, segment.last_observed_at
    assert isinstance(segment, ProjectedGap)
    return segment.started_at, None


@pytest.mark.parametrize("seed", range(50))
def test_projected_segments_of_an_account_do_not_overlap(seed: int) -> None:
    rng = random.Random(seed)
    devices = [UUID(int=rng.getrandbits(128)) for _ in range(rng.randint(1, 3))]
    records = random_records(rng, devices)
    now = records[-1].observed_at + timedelta(seconds=rng.choice([0, 30, 3600]))

    spans = [span(segment) for segment in project_events(records, now=now)]

    for started_at, ended_at in spans:
        assert ended_at is None or started_at <= ended_at
    for (_, previous_end), (next_start, _) in pairwise(spans):
        # Equal times touch: a zero-second segment shares its instant.
        assert previous_end is not None, "an open gap is only ever last"
        assert previous_end <= next_start
