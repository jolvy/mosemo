import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mosemo.identity.application.devices import RegisterDevice
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.infrastructure.devices import DeviceRepository
from mosemo.identity.infrastructure.tables import devices
from mosemo.shared.application.events import EventDispatcher
from tests.identity_support import delete_accounts


@pytest.mark.asyncio
async def test_device_create_is_idempotent_within_account(
    integration_session: AsyncSession,
) -> None:
    first_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject=f"device-first-{uuid4()}",
    )
    integration_session.add(first_account)
    second_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject=f"device-second-{uuid4()}",
    )
    integration_session.add(second_account)
    await integration_session.flush()
    first_account_id = first_account.id
    second_account_id = second_account.id
    await integration_session.commit()
    service = RegisterDevice(devices=DeviceRepository(), dispatcher=EventDispatcher())
    shared_key = uuid4()

    first = await service.execute(
        integration_session,
        account_id=first_account_id,
        idempotency_key=shared_key,
    )
    retry = await service.execute(
        integration_session,
        account_id=first_account_id,
        idempotency_key=shared_key,
    )
    different_key = await service.execute(
        integration_session,
        account_id=first_account_id,
        idempotency_key=uuid4(),
    )
    different_account = await service.execute(
        integration_session,
        account_id=second_account_id,
        idempotency_key=shared_key,
    )

    assert retry == first
    assert first.version == 7
    assert different_key != first
    assert different_account != first
    count = await integration_session.scalar(
        select(func.count())
        .select_from(devices)
        .where(devices.c.account_id.in_([first_account_id, second_account_id]))
    )
    assert count == 3


@pytest.mark.asyncio
async def test_concurrent_device_create_retries_return_one_id(
    integration_database_url: str,
) -> None:
    engine = create_async_engine(integration_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    account_id: UUID | None = None
    try:
        async with session_factory() as setup_session:
            account = Account(
                provider=AccountProvider.KAKAO,
                provider_subject=f"device-concurrent-{uuid4()}",
            )
            setup_session.add(account)
            await setup_session.commit()
            account_id = account.id

        idempotency_key = uuid4()

        async def register():
            async with session_factory() as session:
                return await RegisterDevice(
                    devices=DeviceRepository(), dispatcher=EventDispatcher()
                ).execute(
                    session,
                    account_id=account_id,
                    idempotency_key=idempotency_key,
                )

        first, second = await asyncio.gather(register(), register())

        assert first == second
        async with session_factory() as verification_session:
            count = await verification_session.scalar(
                select(func.count())
                .select_from(devices)
                .where(devices.c.account_id == account_id)
                .where(devices.c.idempotency_key == idempotency_key)
            )
            assert count == 1
    finally:
        if account_id is not None:
            async with session_factory() as cleanup_session:
                await cleanup_session.execute(delete_accounts(account_id))
                await cleanup_session.commit()
        await engine.dispose()
