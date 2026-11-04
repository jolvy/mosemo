import gc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Column, MetaData, String, Table, Uuid, func, insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import registry

from mosemo.infra.transactions import (
    TransactionAlreadyOpenError,
    read_snapshot,
    record_event,
    reset_session,
    unit_of_work,
)
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.domain.aggregates import AggregateRoot
from mosemo.shared.domain.events import DomainEvent

# Test-only table and aggregate on their own MetaData, so the application
# metadata that Alembic compares stays unchanged.
test_metadata = MetaData()
notes = Table(
    "transaction_test_notes",
    test_metadata,
    Column("id", Uuid, primary_key=True),
    Column("body", String, nullable=False),
)
marks = Table(
    "transaction_test_marks",
    test_metadata,
    Column("id", Uuid, primary_key=True, default=uuid4),
    Column("note_id", Uuid, nullable=False),
    Column("kind", String, nullable=False),
)


@dataclass(frozen=True)
class NoteWritten(DomainEvent):
    note_id: UUID
    body: str


@dataclass(frozen=True)
class NoteEchoed(DomainEvent):
    note_id: UUID


@dataclass(eq=False, repr=False, kw_only=True)
class Note(AggregateRoot):
    id: UUID = field(default_factory=uuid4)
    body: str

    def write(self, body: str) -> None:
        self.body = body
        self.events.append(NoteWritten(note_id=self.id, body=body))


registry(metadata=test_metadata).map_imperatively(Note, notes)


@pytest_asyncio.fixture
async def db_session(integration_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(integration_database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            await connection.run_sync(test_metadata.create_all)
            session = AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


class Recorder:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def __call__(self, event: DomainEvent, db_session: AsyncSession) -> None:
        self.events.append(event)


def recording_dispatcher() -> tuple[EventDispatcher, Recorder]:
    dispatcher = EventDispatcher()
    recorder = Recorder()
    dispatcher.subscribe(NoteWritten, recorder)
    dispatcher.subscribe(NoteEchoed, recorder)
    return dispatcher, recorder


async def count_rows(db_session: AsyncSession, table: Table) -> int:
    async with read_snapshot(db_session):
        return (
            await db_session.execute(select(func.count()).select_from(table))
        ).scalar_one()


def new_note(body: str) -> Note:
    note = Note(body="")
    note.write(body)
    return note


@pytest.mark.asyncio
async def test_before_commit_handler_runs_in_the_same_transaction(
    db_session: AsyncSession,
) -> None:
    dispatcher = EventDispatcher()
    seen: list[str] = []

    async def mark(event: NoteWritten, db_session: AsyncSession) -> None:
        # The source change is visible because it is the same transaction.
        seen.append(
            (
                await db_session.execute(
                    select(notes.c.body).where(notes.c.id == event.note_id)
                )
            ).scalar_one()
        )
        await db_session.execute(
            insert(marks).values(note_id=event.note_id, kind="written")
        )

    dispatcher.subscribe(NoteWritten, mark)

    async with unit_of_work(db_session, dispatcher):
        db_session.add(new_note("hello"))

    assert seen == ["hello"]
    assert await count_rows(db_session, notes) == 1
    assert await count_rows(db_session, marks) == 1


@pytest.mark.asyncio
async def test_handler_failure_rolls_back_the_source_change(
    db_session: AsyncSession,
) -> None:
    dispatcher = EventDispatcher()

    async def fail(event: NoteWritten, db_session: AsyncSession) -> None:
        await db_session.execute(
            insert(marks).values(note_id=event.note_id, kind="written")
        )
        raise RuntimeError("handler failed")

    dispatcher.subscribe(NoteWritten, fail)
    note = new_note("hello")

    with pytest.raises(RuntimeError, match="handler failed"):
        async with unit_of_work(db_session, dispatcher):
            db_session.add(note)

    assert note.events == []
    assert await count_rows(db_session, notes) == 0
    assert await count_rows(db_session, marks) == 0


@pytest.mark.asyncio
async def test_follow_up_events_are_dispatched_until_none_are_left(
    db_session: AsyncSession,
) -> None:
    dispatcher, recorder = recording_dispatcher()
    follow_up = new_note("follow-up")

    async def echo(event: NoteWritten, db_session: AsyncSession) -> None:
        record_event(db_session, NoteEchoed(note_id=event.note_id))
        if event.body == "first":
            db_session.add(follow_up)

    dispatcher.subscribe(NoteWritten, echo)
    first = new_note("first")

    async with unit_of_work(db_session, dispatcher):
        db_session.add(first)

    assert recorder.events == [
        NoteWritten(note_id=first.id, body="first"),
        # Second round: tracked roots first, then record_event entries.
        NoteWritten(note_id=follow_up.id, body="follow-up"),
        NoteEchoed(note_id=first.id),
        NoteEchoed(note_id=follow_up.id),
    ]
    assert await count_rows(db_session, notes) == 2


@pytest.mark.asyncio
async def test_rollback_discards_pending_events(db_session: AsyncSession) -> None:
    dispatcher, recorder = recording_dispatcher()
    note = new_note("hello")

    async def write_then_fail() -> None:
        async with unit_of_work(db_session, dispatcher):
            db_session.add(note)
            record_event(db_session, NoteEchoed(note_id=note.id))
            raise LookupError

    with pytest.raises(LookupError):
        await write_then_fail()

    async with unit_of_work(db_session, dispatcher):
        pass

    assert note.events == []
    assert recorder.events == []


@pytest.mark.asyncio
async def test_two_transactions_in_one_session_dispatch_each_event_once(
    db_session: AsyncSession,
) -> None:
    dispatcher, recorder = recording_dispatcher()
    note_id = uuid4()

    async with unit_of_work(db_session, dispatcher):
        note = Note(id=note_id, body="")
        note.write("first")
        db_session.add(note)
        await db_session.flush()
        # Only the tracking set keeps the root alive after this point.
        del note
        gc.collect()

    gc.collect()

    async with unit_of_work(db_session, dispatcher):
        same = await db_session.get(Note, note_id)
        assert same is not None
        same.write("second")

    assert recorder.events == [
        NoteWritten(note_id=note_id, body="first"),
        NoteWritten(note_id=note_id, body="second"),
    ]


@pytest.mark.asyncio
async def test_root_from_identity_map_stays_tracked_in_the_next_transaction(
    db_session: AsyncSession,
) -> None:
    dispatcher, recorder = recording_dispatcher()
    note = new_note("first")

    async with unit_of_work(db_session, dispatcher):
        db_session.add(note)

    # No load event fires again; only the session-long tracking set finds it.
    async with unit_of_work(db_session, dispatcher):
        note.write("second")

    assert recorder.events == [
        NoteWritten(note_id=note.id, body="first"),
        NoteWritten(note_id=note.id, body="second"),
    ]


@pytest.mark.asyncio
async def test_merged_new_root_gets_events_and_is_tracked(
    db_session: AsyncSession,
) -> None:
    dispatcher, recorder = recording_dispatcher()

    async with unit_of_work(db_session, dispatcher):
        merged = await db_session.merge(Note(body="merged"))
        assert merged.events == []
        merged.write("written")

    assert recorder.events == [NoteWritten(note_id=merged.id, body="written")]


@pytest.mark.asyncio
async def test_orm_insert_returning_root_is_tracked(db_session: AsyncSession) -> None:
    dispatcher, recorder = recording_dispatcher()
    note_id = uuid4()

    async with unit_of_work(db_session, dispatcher):
        note = (
            await db_session.execute(
                insert(Note).values(id=note_id, body="inserted").returning(Note)
            )
        ).scalar_one()
        assert note.events == []
        note.write("written")
        del note
        gc.collect()

    assert recorder.events == [NoteWritten(note_id=note_id, body="written")]


@pytest.mark.asyncio
async def test_root_loaded_from_database_gets_empty_events_and_is_tracked(
    db_session: AsyncSession,
) -> None:
    dispatcher, recorder = recording_dispatcher()
    async with unit_of_work(db_session, dispatcher):
        db_session.add(Note(id=(note_id := uuid4()), body="stored"))

    db_session.expunge_all()

    async with unit_of_work(db_session, dispatcher):
        loaded = (await db_session.execute(select(Note))).scalar_one()
        assert loaded.events == []
        loaded.write("changed")

    assert recorder.events == [NoteWritten(note_id=note_id, body="changed")]


@pytest.mark.asyncio
async def test_entering_with_an_open_transaction_fails(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(select(1))

    with pytest.raises(TransactionAlreadyOpenError):
        async with unit_of_work(db_session, EventDispatcher()):
            pass
    with pytest.raises(TransactionAlreadyOpenError):
        async with read_snapshot(db_session):
            pass


@pytest.mark.asyncio
async def test_read_snapshot_is_read_only_and_dispatches_nothing(
    db_session: AsyncSession,
) -> None:
    dispatcher, recorder = recording_dispatcher()
    note = new_note("hello")

    with pytest.raises(DBAPIError, match="read-only transaction"):
        async with read_snapshot(db_session):
            db_session.add(note)

    async with unit_of_work(db_session, dispatcher):
        pass

    assert note.events == []
    assert recorder.events == []
    assert await count_rows(db_session, notes) == 0


@pytest.mark.asyncio
async def test_reset_session_forgets_loaded_and_tracked_roots(
    db_session: AsyncSession,
) -> None:
    dispatcher, recorder = recording_dispatcher()
    note = new_note("first")
    async with unit_of_work(db_session, dispatcher):
        db_session.add(note)

    reset_session(db_session)
    # A forgotten root is detached: its later events are not dispatched.
    note.write("detached")
    async with unit_of_work(db_session, dispatcher):
        loaded = (await db_session.execute(select(Note))).scalar_one()

    assert loaded is not note
    assert recorder.events == [NoteWritten(note_id=note.id, body="first")]


@pytest.mark.asyncio
async def test_reset_session_with_an_open_transaction_fails(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(select(1))

    with pytest.raises(TransactionAlreadyOpenError):
        reset_session(db_session)
