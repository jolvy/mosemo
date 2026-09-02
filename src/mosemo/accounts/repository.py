from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account, AccountProvider


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def save(
        self,
        *,
        provider: AccountProvider,
        provider_subject: str,
    ) -> Account:
        account = Account(provider=provider, provider_subject=provider_subject)
        self._session.add(account)
        return account

    async def find(
        self,
        *,
        provider: AccountProvider,
        provider_subject: str,
    ) -> Account | None:
        result = await self._session.scalars(
            select(Account)
            .where(Account.provider == provider)
            .where(Account.provider_subject == provider_subject)
        )
        return result.one_or_none()

    async def find_by_id(self, account_id: UUID) -> Account | None:
        return await self._session.get(Account, account_id)
