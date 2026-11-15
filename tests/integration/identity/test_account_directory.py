from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.identity.bootstrap import create_account_directory
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.public import AccountNotFoundError
from mosemo.shared.domain.timezones import Timezone
from tests.support.identity import delete_accounts


@pytest.mark.asyncio
async def test_read_timezone_for_share_blocks_timezone_update(
    integration_database_url: str,
) -> None:
    engine = create_async_engine(integration_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    account = Account(
        provider=AccountProvider.KAKAO, provider_subject=f"share-{uuid4()}"
    )
    try:
        async with session_factory() as setup_session:
            setup_session.add(account)
            await setup_session.commit()

        directory = create_account_directory()
        async with session_factory() as reader, reader.begin():
            timezone = await directory.read_timezone_for_share(reader, account.id)
            assert timezone is Timezone.ASIA_SEOUL

            async with session_factory() as writer, writer.begin():
                await writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(DBAPIError) as raised:
                    await writer.execute(
                        text(
                            "UPDATE accounts SET timezone = 'America/New_York' "
                            "WHERE id = :id"
                        ),
                        {"id": account.id},
                    )
                assert getattr(raised.value.orig, "sqlstate", None) == "55P03"
    finally:
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(delete_accounts(account.id))
            await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_read_timezone_for_share_raises_for_missing_account(
    integration_session: AsyncSession,
) -> None:
    with pytest.raises(AccountNotFoundError):
        await create_account_directory().read_timezone_for_share(
            integration_session, uuid4()
        )
