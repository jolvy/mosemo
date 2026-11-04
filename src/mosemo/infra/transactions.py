"""Transaction helpers and aggregate tracking for domain events (ADR 0012, 0013)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.domain.aggregates import AggregateRoot
from mosemo.shared.domain.events import DomainEvent

_TRACKED_ROOTS = "mosemo.tracked_aggregate_roots"
_RECORDED_EVENTS = "mosemo.recorded_events"


class TransactionAlreadyOpenError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "the session already has an open transaction; run every database "
            "access inside unit_of_work or read_snapshot"
        )


def _tracked_roots(session: Session) -> dict[int, AggregateRoot]:
    # Strong references kept for the session lifetime: the identity map is weak,
    # so a root dropped after a flush would otherwise lose its events.
    return session.info.setdefault(_TRACKED_ROOTS, {})


def _recorded_events(session: Session) -> list[DomainEvent]:
    return session.info.setdefault(_RECORDED_EVENTS, [])


@event.listens_for(Session, "loaded_as_persistent")
@event.listens_for(Session, "transient_to_pending")
def _track_root(session: Session, instance: Any) -> None:
    if isinstance(instance, AggregateRoot):
        # ORM loading and merge skip __init__, so the events list may not exist.
        if "events" not in vars(instance):
            instance.events = []
        _tracked_roots(session)[id(instance)] = instance


def record_event(db_session: AsyncSession, domain_event: DomainEvent) -> None:
    """Record an event for a change that has no aggregate root."""
    _recorded_events(db_session.sync_session).append(domain_event)


def _take_events(session: Session) -> list[DomainEvent]:
    taken: list[DomainEvent] = []
    for root in _tracked_roots(session).values():
        taken.extend(root.events)
        root.events.clear()
    recorded = _recorded_events(session)
    taken.extend(recorded)
    recorded.clear()
    return taken


def _discard_events(session: Session) -> None:
    _take_events(session)


async def _dispatch_until_empty(
    db_session: AsyncSession, dispatcher: EventDispatcher
) -> None:
    # Flush first so handlers see the changes that raised the events, even
    # through Core statements that do not autoflush.
    await db_session.flush()
    while events := _take_events(db_session.sync_session):
        for domain_event in events:
            await dispatcher.dispatch(domain_event, db_session)
        await db_session.flush()


def _ensure_no_transaction(db_session: AsyncSession) -> None:
    if db_session.in_transaction():
        raise TransactionAlreadyOpenError


@asynccontextmanager
async def unit_of_work(
    db_session: AsyncSession, dispatcher: EventDispatcher
) -> AsyncIterator[None]:
    """Open a write transaction and dispatch its events before committing.

    BEFORE_COMMIT handlers run in the same transaction until no event is left.
    On an exception the transaction rolls back and pending events are dropped.
    """
    _ensure_no_transaction(db_session)
    try:
        async with db_session.begin():
            yield
            await _dispatch_until_empty(db_session, dispatcher)
    finally:
        # After a commit nothing is left; after a rollback this drops the rest.
        _discard_events(db_session.sync_session)


@asynccontextmanager
async def read_snapshot(db_session: AsyncSession) -> AsyncIterator[None]:
    """Open a read-only transaction; it dispatches no events."""
    _ensure_no_transaction(db_session)
    try:
        async with db_session.begin():
            connection = await db_session.connection()
            await connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            yield
    finally:
        _discard_events(db_session.sync_session)
