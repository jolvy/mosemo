from dataclasses import dataclass, fields

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.shared.application.events import EventDispatcher, Phase
from mosemo.shared.domain.aggregates import AggregateRoot
from mosemo.shared.domain.events import DomainEvent


@dataclass(frozen=True)
class SampleHappened(DomainEvent):
    value: int


@dataclass(frozen=True)
class OtherHappened(DomainEvent):
    value: int


@dataclass(eq=False, repr=False, kw_only=True)
class Sample(AggregateRoot):
    value: int


def test_aggregate_root_starts_with_own_empty_events_outside_init() -> None:
    first = Sample(value=1)
    second = Sample(value=2)
    first.events.append(SampleHappened(value=1))

    assert second.events == []
    assert next(f for f in fields(Sample) if f.name == "events").init is False


def test_domain_event_is_frozen() -> None:
    assert DomainEvent.__dataclass_params__.frozen
    assert SampleHappened.__dataclass_params__.frozen


@pytest.mark.parametrize("phase", [Phase.AFTER_COMMIT, Phase.AFTER_ROLLBACK])
def test_subscribe_rejects_unsupported_phase_immediately(phase: Phase) -> None:
    async def handler(event: SampleHappened, db_session: AsyncSession) -> None:
        raise AssertionError

    with pytest.raises(ValueError, match=phase.name):
        EventDispatcher().subscribe(SampleHappened, handler, phase=phase)


@pytest.mark.asyncio
async def test_dispatch_runs_handlers_of_the_event_type_in_order() -> None:
    calls: list[tuple[str, DomainEvent, AsyncSession]] = []
    dispatcher = EventDispatcher()
    session = AsyncSession()

    async def first(event: SampleHappened, db_session: AsyncSession) -> None:
        calls.append(("first", event, db_session))

    async def second(event: SampleHappened, db_session: AsyncSession) -> None:
        calls.append(("second", event, db_session))

    async def other(event: OtherHappened, db_session: AsyncSession) -> None:
        calls.append(("other", event, db_session))

    dispatcher.subscribe(SampleHappened, first)
    dispatcher.subscribe(SampleHappened, second, phase=Phase.BEFORE_COMMIT)
    dispatcher.subscribe(OtherHappened, other)
    event = SampleHappened(value=1)

    await dispatcher.dispatch(event, session)

    assert calls == [("first", event, session), ("second", event, session)]


@pytest.mark.asyncio
async def test_dispatch_without_handlers_does_nothing() -> None:
    await EventDispatcher().dispatch(SampleHappened(value=1), AsyncSession())
