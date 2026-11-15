"""`ChangeAccountTimezone` on PostgreSQL: the UPDATE, the event and the lock."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.identity.application.accounts import (
    AccountNotFoundError,
    ChangeAccountTimezone,
)
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.events import AccountTimezoneChanged
from mosemo.identity.infrastructure.accounts import AccountRepository
from mosemo.identity.infrastructure.tables import accounts
from mosemo.platform.database.persistence import account_lock_key
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.domain.timezones import Timezone
from tests.support.identity import delete_accounts

pytestmark = pytest.mark.asyncio

NEW_YORK = Timezone.AMERICA_NEW_YORK


@pytest_asyncio.fixture
async def factory(
    integration_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(integration_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def account_id(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[UUID]:
    async with factory() as session:
        account = Account(provider=AccountProvider.KAKAO, provider_subject=str(uuid4()))
        session.add(account)
        await session.commit()
    try:
        yield account.id
    finally:
        async with factory() as session:
            await session.execute(delete_accounts(account.id))
            await session.commit()


class Recorder:
    def __init__(self) -> None:
        self.events: list[AccountTimezoneChanged] = []
        self.dispatcher = EventDispatcher()
        self.dispatcher.subscribe(AccountTimezoneChanged, self.record)

    async def record(self, event: AccountTimezoneChanged, db_session) -> None:
        # BEFORE_COMMIT: the UPDATE is already visible in the same transaction.
        stored = await db_session.scalar(
            select(accounts.c.timezone).where(accounts.c.id == event.account_id)
        )
        assert stored == event.current.value
        self.events.append(event)


async def stored_timezone(
    factory: async_sessionmaker[AsyncSession], account_id: UUID
) -> str | None:
    async with factory() as session:
        return await session.scalar(
            select(accounts.c.timezone).where(accounts.c.id == account_id)
        )


async def change(
    factory: async_sessionmaker[AsyncSession],
    recorder: Recorder,
    account_id: UUID,
    timezone: Timezone,
) -> None:
    async with factory() as session:
        await ChangeAccountTimezone(
            accounts=AccountRepository(), dispatcher=recorder.dispatcher
        ).execute(session, account_id=account_id, timezone=timezone)


async def test_change_updates_the_timezone_and_dispatches_the_event_once(
    factory: async_sessionmaker[AsyncSession], account_id: UUID
) -> None:
    recorder = Recorder()

    await change(factory, recorder, account_id, NEW_YORK)
    await change(factory, recorder, account_id, NEW_YORK)

    assert await stored_timezone(factory, account_id) == NEW_YORK.value
    assert recorder.events == [
        AccountTimezoneChanged(
            account_id=account_id, previous=Timezone.ASIA_SEOUL, current=NEW_YORK
        )
    ]


async def test_change_of_an_unknown_account_raises_not_found(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(AccountNotFoundError):
        await change(factory, Recorder(), uuid4(), NEW_YORK)


async def test_change_waits_for_the_account_lock_of_a_source_write(
    factory: async_sessionmaker[AsyncSession], account_id: UUID
) -> None:
    """The use case takes the same advisory lock as observation's writers."""
    recorder = Recorder()
    async with factory() as holder, holder.begin():
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": account_lock_key(account_id)},
        )
        waiting = asyncio.ensure_future(change(factory, recorder, account_id, NEW_YORK))
        async with asyncio.timeout(2):
            while not await advisory_waiters(factory):
                await asyncio.sleep(0.01)
        assert not waiting.done()
        assert await stored_timezone(factory, account_id) == Timezone.ASIA_SEOUL.value

    await waiting
    assert await stored_timezone(factory, account_id) == NEW_YORK.value


async def advisory_waiters(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        return await session.scalar(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
            )
        )
