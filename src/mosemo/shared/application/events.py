from collections.abc import Awaitable, Callable
from enum import Enum, auto
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.shared.domain.events import DomainEvent

type Handler[E: DomainEvent] = Callable[[E, AsyncSession], Awaitable[None]]


class Phase(Enum):
    BEFORE_COMMIT = auto()
    AFTER_COMMIT = auto()
    AFTER_ROLLBACK = auto()


class EventDispatcher:
    """Runs subscribed handlers for domain events (ADR 0012).

    Only BEFORE_COMMIT is implemented: handlers run inline in the same session
    and transaction, so a failing handler rolls back the source change too.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[Handler[Any]]] = {}

    def subscribe[E: DomainEvent](
        self,
        event_type: type[E],
        handler: Handler[E],
        *,
        phase: Phase = Phase.BEFORE_COMMIT,
    ) -> None:
        if phase is not Phase.BEFORE_COMMIT:
            raise ValueError(f"{phase.name} handlers are not supported yet")
        self._handlers.setdefault(event_type, []).append(handler)

    async def dispatch(self, event: DomainEvent, db_session: AsyncSession) -> None:
        for handler in self._handlers.get(type(event), []):
            await handler(event, db_session)
