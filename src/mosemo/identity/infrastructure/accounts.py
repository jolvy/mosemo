from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.domain.accounts import Account
from mosemo.identity.infrastructure.tables import accounts
from mosemo.platform.database.persistence import lock_account


class AccountRepository:
    async def get_or_create(
        self, db_session: AsyncSession, account: Account
    ) -> tuple[Account, bool]:
        """Insert the account unless its provider identity exists; return it and
        whether this call created it."""
        created = await db_session.scalar(
            insert(Account)
            .values(
                id=account.id,
                provider=account.provider,
                provider_subject=account.provider_subject,
            )
            .on_conflict_do_nothing(
                index_elements=[accounts.c.provider, accounts.c.provider_subject]
            )
            .returning(Account)
        )
        if created is not None:
            return created, True
        existing = await db_session.scalar(
            select(Account)
            .where(accounts.c.provider == account.provider)
            .where(accounts.c.provider_subject == account.provider_subject)
        )
        if existing is None:
            raise RuntimeError("account conflict could not be resolved")
        return existing, False

    async def find(self, db_session: AsyncSession, account_id: UUID) -> Account | None:
        return await db_session.get(Account, account_id, populate_existing=True)

    async def lock_account(self, db_session: AsyncSession, account_id: UUID) -> None:
        """Hold the account advisory lock (the one every source write of the
        account takes) until the transaction ends."""
        await lock_account(db_session, account_id)
