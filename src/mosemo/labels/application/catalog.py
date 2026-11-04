from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.labels.infrastructure.queries import LabelQuery


@dataclass(frozen=True, slots=True)
class ActiveLabel:
    id: UUID
    display_name: str


class LabelCatalog:
    """Label checks and names for other contexts inside their use case transaction."""

    def __init__(self, *, labels: LabelQuery) -> None:
        self._labels = labels

    async def is_active(
        self, db_session: AsyncSession, *, account_id: UUID, label_id: UUID
    ) -> bool:
        """Whether the account owns the label and has not archived it."""
        return await self._labels.is_active(
            db_session, account_id=account_id, label_id=label_id
        )

    async def list_active(
        self, db_session: AsyncSession, account_id: UUID
    ) -> list[ActiveLabel]:
        """The account's active labels ordered by display name, then ID."""
        rows = await self._labels.list_active(db_session, account_id)
        return [ActiveLabel(id=label_id, display_name=name) for label_id, name in rows]

    async def find_names(
        self, db_session: AsyncSession, *, account_id: UUID, label_ids: set[UUID]
    ) -> dict[UUID, str]:
        """Display names of the account's labels, archived ones included.

        Labels that do not exist or belong to another account are left out.
        """
        return await self._labels.find_names(
            db_session, account_id=account_id, label_ids=label_ids
        )
