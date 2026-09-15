from uuid import UUID

from mosemo.accounts.models import Account
from mosemo.accounts.repository import AccountRepository


class AccountNotFoundError(Exception):
    pass


class AccountService:
    def __init__(self, *, account_repository: AccountRepository) -> None:
        self._account_repository = account_repository

    async def get_account(self, account_id: UUID) -> Account:
        account = await self._account_repository.find_by_id(account_id)
        if account is None:
            raise AccountNotFoundError
        return account
