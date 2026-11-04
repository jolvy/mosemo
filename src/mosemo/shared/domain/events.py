from dataclasses import dataclass


@dataclass(frozen=True)
class DomainEvent:
    """Base of the events an aggregate records when its state changes (ADR 0012)."""
