from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from mosemo.shared.domain.events import DomainEvent


@dataclass(frozen=True)
class FocusSessionCompleted(DomainEvent):
    """A focus session was completed for the first time."""

    account_id: UUID
    focus_session_id: UUID
    started_at: datetime
    ended_at: datetime
