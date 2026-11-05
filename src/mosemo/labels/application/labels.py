from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.public import AccountCreated, AccountDirectory
from mosemo.labels.domain.labels import Label, default_labels
from mosemo.labels.infrastructure.labels import LabelRepository
from mosemo.platform.database.transactions import read_snapshot
from mosemo.shared.application.use_case import UseCase


class ListLabels(UseCase):
    def __init__(self, *, labels: LabelRepository, accounts: AccountDirectory) -> None:
        self._labels = labels
        self._accounts = accounts

    async def execute(self, db_session: AsyncSession, account_id: UUID) -> list[Label]:
        """Return the account's active and archived labels.

        Raises identity's `AccountNotFoundError` when the account does not exist.
        """
        async with read_snapshot(db_session):
            await self._accounts.ensure_exists(db_session, account_id)
            return await self._labels.list_owned(db_session, account_id)


class DefaultLabels:
    """Creates the default labels of a new account in the sign-up transaction."""

    def __init__(self, *, labels: LabelRepository) -> None:
        self._labels = labels

    async def on_account_created(
        self, event: AccountCreated, db_session: AsyncSession
    ) -> None:
        self._labels.add_all(db_session, default_labels(event.account_id))
