from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from mosemo.shared.domain.events import DomainEvent


@dataclass(frozen=True)
class LabelConfirmed(DomainEvent):
    """A segment's confirmation was created or changed.

    `segment_ended_at` is the segment's end, else its last observation.
    """

    account_id: UUID
    segment_started_at: datetime
    segment_ended_at: datetime
