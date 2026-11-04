from dataclasses import dataclass
from uuid import UUID

from mosemo.shared.domain.events import DomainEvent


@dataclass(frozen=True)
class AccountCreated(DomainEvent):
    """A new account was created on its first sign-in."""

    account_id: UUID
