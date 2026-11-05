from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.domain.accounts import Account
from mosemo.identity.infrastructure.accounts import AccountRepository
from mosemo.identity.infrastructure.queries import AccountQuery
from mosemo.platform.database.transactions import read_snapshot
from mosemo.shared.application.use_case import UseCase
from mosemo.shared.domain.timezones import Timezone


class AccountNotFoundError(Exception):
    """The account does not exist although its access token is valid."""


class GetAccount(UseCase):
    def __init__(self, *, accounts: AccountRepository) -> None:
        self._accounts = accounts

    async def execute(self, db_session: AsyncSession, account_id: UUID) -> Account:
        async with read_snapshot(db_session):
            account = await self._accounts.find(db_session, account_id)
        if account is None:
            raise AccountNotFoundError
        return account


class AccountDirectory:
    """Account checks for other contexts inside their use case transaction."""

    def __init__(self, *, accounts: AccountQuery) -> None:
        self._accounts = accounts

    async def ensure_exists(self, db_session: AsyncSession, account_id: UUID) -> None:
        if not await self._accounts.exists(db_session, account_id):
            raise AccountNotFoundError

    async def get_timezone(
        self, db_session: AsyncSession, account_id: UUID
    ) -> Timezone:
        timezone = await self._accounts.find_timezone(db_session, account_id)
        if timezone is None:
            raise AccountNotFoundError
        return timezone
