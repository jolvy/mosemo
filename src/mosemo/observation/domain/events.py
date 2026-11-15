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


@dataclass(frozen=True)
class TimelineChanged(DomainEvent):
    """The account's stored timeline segments changed within `[changed_from,
    changed_to]`, covering both the removed and the new segments."""

    account_id: UUID
    changed_from: datetime
    changed_to: datetime
