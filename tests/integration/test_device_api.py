from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.api import v1_api_router
from mosemo.auth.tokens import TokenService
from mosemo.config import Config, get_config
from mosemo.database import get_session
from mosemo.devices.repository import DeviceRepository
from mosemo.exception_handlers import register_exception_handlers


@pytest_asyncio.fixture
async def device_api(
    integration_database_url: str, config: Config
) -> AsyncIterator[
    tuple[httpx2.AsyncClient, async_sessionmaker[AsyncSession], list[UUID]]
]:
    engine = create_async_engine(integration_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    account_ids: list[UUID] = []
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_config] = lambda: config

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            yield client, factory, account_ids
    finally:
        if account_ids:
            async with factory() as session:
                await session.execute(
                    delete(Account).where(Account.account_id.in_(account_ids))
                )
                await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_device_registration_and_retries_use_real_dependency_wiring(
    device_api, config: Config
) -> None:
    client, factory, account_ids = device_api
    async with factory() as session:
        for _ in range(2):
            account = AccountRepository(session).save(
                provider=AccountProvider.KAKAO, provider_subject=str(uuid4())
            )
            await session.flush()
            account_ids.append(account.account_id)
        await session.commit()

    key = uuid4()

    async def register(account_id: UUID, key: UUID):
        return await client.post(
            "/api/v1/devices",
            headers={
                "Authorization": "Bearer "
                + TokenService(config.auth).issue_access_token(account_id),
                "Idempotency-Key": str(key),
            },
        )

    first = await register(account_ids[0], key)
    retry = await register(account_ids[0], key)
    other_key = await register(account_ids[0], uuid4())
    other_account = await register(account_ids[1], key)

    assert [
        response.status_code for response in (first, retry, other_key, other_account)
    ] == [201, 201, 201, 201]
    assert set(first.json()) == {"deviceId"}
    assert retry.json() == first.json()
    device_id = UUID(first.json()["deviceId"])
    assert device_id.version == 7
    assert (
        len(
            {
                device_id,
                UUID(other_key.json()["deviceId"]),
                UUID(other_account.json()["deviceId"]),
            }
        )
        == 3
    )
    async with factory() as session:
        repository = DeviceRepository(session)
        assert (
            await repository.find_owned_by_id(
                account_id=account_ids[0], device_id=device_id
            )
            is not None
        )
        assert (
            await repository.find_owned_by_id(
                account_id=account_ids[1], device_id=device_id
            )
            is None
        )


@pytest.mark.asyncio
async def test_valid_token_for_missing_account_preserves_internal_error(
    device_api, config: Config
) -> None:
    client, _, _ = device_api
    response = await client.post(
        "/api/v1/devices",
        headers={
            "Authorization": "Bearer "
            + TokenService(config.auth).issue_access_token(uuid4()),
            "Idempotency-Key": str(uuid4()),
        },
    )

    assert response.status_code == 500
    assert response.json()["error"]["status"] == "INTERNAL_SERVER_ERROR"
