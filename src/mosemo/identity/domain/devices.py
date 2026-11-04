from dataclasses import dataclass, field
from uuid import UUID, uuid7

from mosemo.shared.domain.aggregates import AggregateRoot


@dataclass(eq=False, repr=False, kw_only=True)
class Device(AggregateRoot):
    id: UUID = field(default_factory=uuid7)
    account_id: UUID
    idempotency_key: UUID
