from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.labels.infrastructure.tables import labels


class LabelQuery:
    async def is_active(
        self, db_session: AsyncSession, *, account_id: UUID, label_id: UUID
    ) -> bool:
        return bool(
            await db_session.scalar(
                select(
                    exists()
                    .where(labels.c.account_id == account_id)
                    .where(labels.c.id == label_id)
                    .where(labels.c.archived_at.is_(None))
                )
            )
        )

    async def list_active(
        self, db_session: AsyncSession, account_id: UUID
    ) -> list[tuple[UUID, str]]:
        result = await db_session.execute(
            select(labels.c.id, labels.c.display_name)
            .where(labels.c.account_id == account_id)
            .where(labels.c.archived_at.is_(None))
            .order_by(labels.c.display_name, labels.c.id)
        )
        return [(row.id, row.display_name) for row in result]

    async def find_names(
        self, db_session: AsyncSession, *, account_id: UUID, label_ids: set[UUID]
    ) -> dict[UUID, str]:
        if not label_ids:
            return {}
        result = await db_session.execute(
            select(labels.c.id, labels.c.display_name)
            .where(labels.c.account_id == account_id)
            .where(labels.c.id.in_(label_ids))
        )
        return {row.id: row.display_name for row in result}
