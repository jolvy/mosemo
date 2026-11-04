from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.labels.domain.labels import Label
from mosemo.labels.infrastructure.tables import labels


class LabelRepository:
    def add_all(self, db_session: AsyncSession, new_labels: list[Label]) -> None:
        db_session.add_all(new_labels)

    async def list_owned(
        self, db_session: AsyncSession, account_id: UUID
    ) -> list[Label]:
        result = await db_session.scalars(
            select(Label)
            .where(labels.c.account_id == account_id)
            .order_by(labels.c.display_name, labels.c.id)
        )
        return list(result.all())
