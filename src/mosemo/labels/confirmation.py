from dataclasses import dataclass
from uuid import UUID

from mosemo.labels.models import ActivityLabelConfirmation, ActivityLabelProposal


@dataclass(frozen=True, slots=True)
class ConfirmationTarget:
    segment_id: UUID
    first_event_id: UUID
    segment_version: str
    label_id: UUID | None

    def matches(self, confirmation: ActivityLabelConfirmation | None) -> bool:
        return (
            confirmation is not None
            and confirmation.segment_version == self.segment_version
            and confirmation.label_id == self.label_id
        )


@dataclass(frozen=True, slots=True)
class SavedConfirmation:
    segment_id: UUID
    segment_version: str
    confirmation: ActivityLabelConfirmation
    proposal: ActivityLabelProposal | None
