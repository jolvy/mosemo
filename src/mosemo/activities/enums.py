from enum import StrEnum


class RecordType(StrEnum):
    ACTIVITY_OBSERVATION = "activity_observation"
    COLLECTION_STATE_CHANGED = "collection_state_changed"


class SegmentType(StrEnum):
    ACTIVITY = "activity"
    CAPTURE_GAP = "capture_gap"


class ActivityTimelineKind(StrEnum):
    CAPTURE_GAP = "capture_gap"
    OPAQUE_ACTIVITY = "opaque_activity"
    IN_PROGRESS_ACTIVITY = "in_progress_activity"
    CLOSED_DETAILED_ACTIVITY = "closed_detailed_activity"


class CollectionState(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
