from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from mosemo.shared.domain.aggregates import AggregateRoot
from mosemo.shared.domain.timezones import Timezone

MAX_OBSERVATION_GAP = timedelta(seconds=60)
# How far before the server's acceptance time a record may have been observed.
ACCEPTANCE_WINDOW = timedelta(hours=24)


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


class ActivityRecordOutsideAcceptanceWindowError(Exception):
    """Records observed outside the acceptance window, by position in the batch."""

    def __init__(self, indexes: Iterable[int]) -> None:
        self.indexes = tuple(indexes)
        super().__init__(f"records outside the acceptance window: {self.indexes}")


def is_within_acceptance_window(
    observed_at: datetime, *, accepted_at: datetime
) -> bool:
    """Whether a record was observed at most 24 hours before acceptance and not
    after it. Both ends are included; aware datetimes compare as absolute time.
    """
    return accepted_at - ACCEPTANCE_WINDOW <= observed_at <= accepted_at


def ensure_within_acceptance_window(
    observed_at: Iterable[datetime], *, accepted_at: datetime
) -> None:
    """Raise `ActivityRecordOutsideAcceptanceWindowError` naming every record,
    in batch order, that was observed outside the acceptance window.
    """
    outside = [
        index
        for index, value in enumerate(observed_at)
        if not is_within_acceptance_window(value, accepted_at=accepted_at)
    ]
    if outside:
        raise ActivityRecordOutsideAcceptanceWindowError(outside)


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
