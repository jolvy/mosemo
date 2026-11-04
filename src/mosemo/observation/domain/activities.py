from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from mosemo.shared.domain.aggregates import AggregateRoot
from mosemo.shared.domain.timezones import Timezone

MAX_OBSERVATION_GAP = timedelta(seconds=60)


class RecordType(StrEnum):
    ACTIVITY_OBSERVATION = "activity_observation"
    COLLECTION_STATE_CHANGED = "collection_state_changed"


class CollectionState(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class ActivityDeviceNotFoundError(Exception):
    pass


class ActivityEventIdConflictError(Exception):
    pass


class ActivitySequenceConflictError(Exception):
    pass


@dataclass(eq=False, repr=False, kw_only=True)
class ActivityRecord(AggregateRoot):
    event_id: UUID
    device_id: UUID
    focus_session_id: UUID | None = None
    sequence: int
    record_type: RecordType
    observed_at: datetime
    timezone_id: Timezone
    utc_offset_minutes: int
    payload: dict[str, object]
    received_at: datetime = field(init=False)

    def has_same_content(self, other: ActivityRecord) -> bool:
        return (
            self.event_id,
            self.device_id,
            self.focus_session_id,
            self.sequence,
            self.record_type,
            self.observed_at,
            self.timezone_id,
            self.utc_offset_minutes,
            self.payload,
        ) == (
            other.event_id,
            other.device_id,
            other.focus_session_id,
            other.sequence,
            other.record_type,
            other.observed_at,
            other.timezone_id,
            other.utc_offset_minutes,
            other.payload,
        )

    def is_in_device_order(
        self, *, previous: datetime | None, following: datetime | None
    ) -> bool:
        return (previous is None or previous <= self.observed_at) and (
            following is None or self.observed_at <= following
        )


@dataclass(eq=False, repr=False, kw_only=True)
class ActivityBatchReceipt(AggregateRoot):
    """Consumer-only atomic processing evidence, scoped to an account/device."""

    account_id: UUID
    device_id: UUID
    batch_id: UUID
    fingerprint: str
    results: list[dict[str, str]]
    processed_at: datetime = field(init=False)
