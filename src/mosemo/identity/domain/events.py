from dataclasses import dataclass
from uuid import UUID

from mosemo.shared.domain.events import DomainEvent
from mosemo.shared.domain.timezones import Timezone


@dataclass(frozen=True)
class AccountCreated(DomainEvent):
    """A new account was created on its first sign-in."""

    account_id: UUID


@dataclass(frozen=True)
class AccountTimezoneChanged(DomainEvent):
    """The account timezone changed; past dates follow the new date boundaries."""

    account_id: UUID
    previous: Timezone
    current: Timezone
