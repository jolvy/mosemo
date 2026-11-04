from dataclasses import dataclass, field

from mosemo.shared.domain.events import DomainEvent


@dataclass(eq=False, repr=False, kw_only=True)
class AggregateRoot:
    """Root that records its domain events; `unit_of_work` collects them."""

    events: list[DomainEvent] = field(default_factory=list, init=False)
