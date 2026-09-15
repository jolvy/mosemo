import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.devices.models import Device
from mosemo.devices.repository import DeviceRepository
from mosemo.devices.service import DeviceService


@pytest.mark.asyncio
async def test_device_create_is_idempotent_within_account(
    integration_session: AsyncSession,
) -> None:
    first_account = AccountRepository(integration_session).save(
        provider=AccountProvider.KAKAO,
        provider_subject=f"device-first-{uuid4()}",
    )
    second_account = AccountRepository(integration_session).save(
        provider=AccountProvider.KAKAO,
        provider_subject=f"device-second-{uuid4()}",
    )
    await integration_session.flush()
    first_account_id = first_account.account_id
    second_account_id = second_account.account_id
    await integration_session.commit()
    service = DeviceService(
        session=integration_session,
        repository=DeviceRepository(integration_session),
    )
    shared_key = uuid4()

    first = await service.create_device(
        account_id=first_account_id,
        idempotency_key=shared_key,
    )
    retry = await service.create_device(
        account_id=first_account_id,
        idempotency_key=shared_key,
    )
    different_key = await service.create_device(
        account_id=first_account_id,
        idempotency_key=uuid4(),
    )
    different_account = await service.create_device(
        account_id=second_account_id,
        idempotency_key=shared_key,
    )

    assert retry == first
    assert first.device_id.version == 7
    assert different_key.device_id != first.device_id
    assert different_account.device_id != first.device_id
    count = await integration_session.scalar(
        select(func.count())
        .select_from(Device)
        .where(Device.account_id.in_([first_account_id, second_account_id]))
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
            account = AccountRepository(setup_session).save(
                provider=AccountProvider.KAKAO,
                provider_subject=f"device-concurrent-{uuid4()}",
            )
            await setup_session.commit()
            account_id = account.account_id

        idempotency_key = uuid4()

        async def register():
            async with session_factory() as session:
                return await DeviceService(
                    session=session,
                    repository=DeviceRepository(session),
                ).create_device(
                    account_id=account_id,
                    idempotency_key=idempotency_key,
                )

        first, second = await asyncio.gather(register(), register())

        assert first == second
        async with session_factory() as verification_session:
            count = await verification_session.scalar(
                select(func.count())
                .select_from(Device)
                .where(Device.account_id == account_id)
                .where(Device.idempotency_key == idempotency_key)
            )
            assert count == 1
    finally:
        if account_id is not None:
            async with session_factory() as cleanup_session:
                await cleanup_session.execute(
                    delete(Account).where(Account.account_id == account_id)
                )
                await cleanup_session.commit()
        await engine.dispose()
