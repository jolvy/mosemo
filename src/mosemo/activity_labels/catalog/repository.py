from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activity_labels.catalog.models import DEFAULT_LABEL_NAMES, Label


class LabelCatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def create_defaults(self, *, account_id: UUID) -> list[Label]:
        labels = [
            Label(
                account_id=account_id,
                display_name=display_name,
            )
            for display_name in DEFAULT_LABEL_NAMES
        ]
        self._session.add_all(labels)
        return labels

    async def find_active_owned(
        self,
        *,
        account_id: UUID,
        label_id: UUID,
    ) -> Label | None:
        return await self._session.scalar(
            select(Label)
            .where(Label.account_id == account_id)
            .where(Label.label_id == label_id)
            .where(Label.archived_at.is_(None))
        )

    async def list_active_owned(self, *, account_id: UUID) -> list[Label]:
        result = await self._session.scalars(
            select(Label)
            .where(Label.account_id == account_id)
            .where(Label.archived_at.is_(None))
            .order_by(Label.display_name, Label.label_id)
        )
        return list(result.all())

    async def list_owned(self, *, account_id: UUID) -> list[Label]:
        result = await self._session.scalars(
            select(Label)
            .where(Label.account_id == account_id)
            .order_by(Label.display_name, Label.label_id)
        )
        return list(result.all())
