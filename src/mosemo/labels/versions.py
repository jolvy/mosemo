import json
from datetime import UTC, datetime
from hashlib import sha256

from mosemo.activities.models import ActivityTimelineSegment


def segment_version(
    segment: ActivityTimelineSegment,
    *,
    ended_at: datetime,
) -> str:
    assert segment.last_event_id is not None
    assert segment.last_observed_at is not None
    assert segment.context is not None
    canonical = json.dumps(
        {
            "context": segment.context,
            "endedAt": ended_at.astimezone(UTC).isoformat(),
            "firstEventId": str(segment.first_event_id),
            "lastEventId": str(segment.last_event_id),
            "lastObservedAt": segment.last_observed_at.astimezone(UTC).isoformat(),
            "startedAt": segment.started_at.astimezone(UTC).isoformat(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
