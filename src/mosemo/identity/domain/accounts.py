from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid7

from mosemo.identity.domain.events import AccountCreated
from mosemo.shared.domain.aggregates import AggregateRoot
from mosemo.shared.domain.timezones import Timezone


class AccountProvider(StrEnum):
    KAKAO = "KAKAO"


@dataclass(eq=False, repr=False, kw_only=True)
class Account(AggregateRoot):
    id: UUID = field(default_factory=uuid7)
    provider: AccountProvider
    provider_subject: str
    timezone: Timezone = field(init=False)
    created_at: datetime = field(init=False)
    last_authenticated_at: datetime = field(init=False)

    def mark_created(self) -> None:
        """Record that this sign-in created the account; the state does not change."""
        self.events.append(AccountCreated(account_id=self.id))

    def authenticate(self, *, now: datetime) -> None:
        self.last_authenticated_at = now
