from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from mosemo.config import get_config


@pytest.fixture(scope="session")
def integration_database_url() -> str:
    return get_config().database.dsn


@pytest_asyncio.fixture
async def integration_session(
    integration_database_url: str,
) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(integration_database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                yield session
            finally:
                await session.close()
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()
