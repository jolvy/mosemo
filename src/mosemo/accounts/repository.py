from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account, AccountProvider


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, account: Account) -> None:
        self._session.add(account)

    async def add_if_absent(
        self,
        *,
        provider: AccountProvider,
        provider_subject: str,
    ) -> Account | None:
        statement = (
            insert(Account)
            .values(provider=provider, provider_subject=provider_subject)
            .on_conflict_do_nothing(
                index_elements=[Account.provider, Account.provider_subject]
            )
            .returning(Account)
        )
        result = await self._session.scalars(statement)
        return result.one_or_none()

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
