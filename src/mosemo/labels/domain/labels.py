from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid7

from mosemo.shared.domain.aggregates import AggregateRoot

DEFAULT_LABEL_NAMES: tuple[str, ...] = ("코딩", "학습", "소통", "쇼핑", "여가")


@dataclass(eq=False, repr=False, kw_only=True)
class Label(AggregateRoot):
    id: UUID = field(default_factory=uuid7)
    account_id: UUID
    display_name: str
    created_at: datetime = field(init=False)
    updated_at: datetime = field(init=False)
    archived_at: datetime | None = None


def default_labels(account_id: UUID) -> list[Label]:
    return [
        Label(account_id=account_id, display_name=display_name)
        for display_name in DEFAULT_LABEL_NAMES
    ]
