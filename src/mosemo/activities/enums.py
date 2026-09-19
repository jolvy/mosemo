from enum import StrEnum


class RecordType(StrEnum):
    ACTIVITY_OBSERVATION = "activity_observation"
    COLLECTION_STATE_CHANGED = "collection_state_changed"


class SegmentType(StrEnum):
    ACTIVITY = "activity"
    CAPTURE_GAP = "capture_gap"


class CollectionState(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
