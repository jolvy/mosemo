from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid7

from mosemo.shared.domain.aggregates import AggregateRoot


class ActivityLabelSegmentNotFoundError(Exception):
    pass


class ActivityLabelSegmentNotLabelableError(Exception):
    pass


class ActivityLabelSegmentChangedError(Exception):
    pass


class ActivityLabelUnavailableError(Exception):
    pass


class ActivityLabelConfirmationConflictError(Exception):
    pass


class ActivityLabelSegmentDuplicateError(Exception):
    pass


@dataclass(eq=False, repr=False, kw_only=True)
class ActivityLabelConfirmation(AggregateRoot):
    """The latest label or unclassified choice for one observed segment.

    `label_id` is `None` for unclassified. The segment is identified by its
    first event, and the choice applies only to `segment_version`.
    """

    id: UUID = field(default_factory=uuid7)
    account_id: UUID
    first_event_id: UUID
    segment_version: str
    label_id: UUID | None
    confirmed_at: datetime
    updated_at: datetime

    def applies_to(self, segment_version: str) -> bool:
        return self.segment_version == segment_version

    def accepts_batch_selection(
        self, *, segment_version: str, label_id: UUID | None
    ) -> bool:
        return self.segment_version != segment_version or self.matches_batch_selection(
            segment_version=segment_version,
            label_id=label_id,
        )

    def matches_batch_selection(
        self, *, segment_version: str, label_id: UUID | None
    ) -> bool:
        return self.segment_version == segment_version and self.label_id == label_id

    def apply_selection(
        self, *, segment_version: str, label_id: UUID | None, now: datetime
    ) -> None:
        if self.segment_version == segment_version and self.label_id == label_id:
            return
        changed_at = max(now, self.updated_at + timedelta(microseconds=1))
        if self.segment_version != segment_version:
            self.segment_version = segment_version
            self.label_id = label_id
            self.confirmed_at = changed_at
            self.updated_at = changed_at
            return
        self.label_id = label_id
        self.updated_at = changed_at
