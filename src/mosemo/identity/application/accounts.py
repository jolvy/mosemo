from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.domain.accounts import Account
from mosemo.identity.infrastructure.accounts import AccountRepository
from mosemo.identity.infrastructure.queries import AccountQuery
from mosemo.platform.database.transactions import read_snapshot, unit_of_work
from mosemo.shared.application.events import EventDispatcher
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


class ChangeAccountTimezone(UseCase):
    """Change the account timezone in one transaction: the account lock, the
    UPDATE, then the BEFORE_COMMIT handlers (analytics marks its past dates
    with the new timezone) before the commit (ADR 0016 「변경 표시」)."""

    def __init__(
        self, *, accounts: AccountRepository, dispatcher: EventDispatcher
    ) -> None:
        self._accounts = accounts
        self._dispatcher = dispatcher

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, timezone: Timezone
    ) -> None:
        async with unit_of_work(db_session, self._dispatcher):
            await self._accounts.lock_account(db_session, account_id)
            account = await self._accounts.find(db_session, account_id)
            if account is None:
                raise AccountNotFoundError
            account.change_timezone(timezone)


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

    async def read_timezone_for_share(
        self, db_session: AsyncSession, account_id: UUID
    ) -> Timezone:
        """The account timezone, holding a FOR SHARE row lock until the caller's
        transaction ends so the timezone cannot change under it."""
        timezone = await self._accounts.find_timezone_for_share(db_session, account_id)
        if timezone is None:
            raise AccountNotFoundError
        return timezone
