from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mosemo.observation.domain.activities import ActivityRecord, RecordType
from mosemo.observation.domain.timeline import ProjectionError, project_events
from mosemo.shared.domain.timezones import Timezone


def record(second, sequence=0, device=None):
    return ActivityRecord(
        event_id=uuid4(),
        device_id=device or uuid4(),
        sequence=sequence,
        record_type=RecordType.ACTIVITY_OBSERVATION,
        observed_at=datetime(2026, 9, 14, 0, 0, second, tzinfo=UTC),
        timezone_id=Timezone.ASIA_SEOUL,
        utc_offset_minutes=540,
        payload={"context": {"kind": "opaque"}},
    )


def test_projection_never_merges_a_negative_observation_gap():
    with pytest.raises(ProjectionError):
        project_events([record(10), record(5)])


@pytest.mark.parametrize("seed", range(20))
def test_incremental_last_state_matches_full_replay_across_mixed_records(seed):
    from dataclasses import asdict
    from datetime import timedelta
    from random import Random

    from mosemo.observation.application.batches import segment_seed
    from mosemo.observation.domain.timeline import (
        ActivityTimelineSegment,
        ProjectedActivity,
        SegmentType,
        TimelineProjection,
    )

    random = Random(seed)
    device = uuid4()
    source = []
    time = datetime(2026, 9, 14, 23, 59, 59, 123456, tzinfo=UTC)
    for sequence in range(50):
        time += timedelta(seconds=random.choice([0, 0.5, 10, 60, 61, 120]))
        state = random.choice(["observation", "observation", "active", "suspended"])
        source.append(
            ActivityRecord(
                event_id=uuid4(),
                device_id=device,
                sequence=sequence * 10,
                observed_at=time,
                timezone_id=Timezone.ASIA_SEOUL,
                utc_offset_minutes=540,
                record_type=RecordType.ACTIVITY_OBSERVATION
                if state == "observation"
                else RecordType.COLLECTION_STATE_CHANGED,
                payload={
                    "context": {"kind": "opaque", "test_context": random.randrange(3)}
                }
                if state == "observation"
                else {"state": state, "reason": "locked"},
            )
        )
    now = time + timedelta(seconds=61)
    expected = project_events(source, now=now)
    incremental = []
    for start in range(0, len(source), 5):
        initial = None
        if incremental:
            last = incremental[-1]
            stored = ActivityTimelineSegment(
                id=uuid4(),
                account_id=uuid4(),
                started_at=last.started_at,
                ended_at=last.ended_at,
                first_event_id=last.first_event_id,
                segment_type=SegmentType.ACTIVITY
                if isinstance(last, ProjectedActivity)
                else SegmentType.CAPTURE_GAP,
                last_event_id=last.last_event_id
                if isinstance(last, ProjectedActivity)
                else None,
                last_observed_at=last.last_observed_at
                if isinstance(last, ProjectedActivity)
                else None,
                context=last.context if isinstance(last, ProjectedActivity) else None,
                reason=last.reason if not isinstance(last, ProjectedActivity) else None,
            )
            initial = segment_seed(stored)
        projection = TimelineProjection(initial)
        for entry in source[start : start + 5]:
            projection.feed(entry)
        incremental = (
            incremental[:-1] + projection.finish(now)
            if incremental
            else projection.finish(now)
        )
    assert [asdict(segment) for segment in incremental] == [
        asdict(segment) for segment in expected
    ]
