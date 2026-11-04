from collections.abc import AsyncIterator
from functools import cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mosemo.infra.config import get_config


# Created on first use so importing this module does not read the configuration.
@cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        get_config().database.dsn,
        pool_pre_ping=True,
    )


@cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        get_engine(),
        expire_on_commit=False,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session
