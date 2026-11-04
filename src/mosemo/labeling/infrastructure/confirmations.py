from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.labeling.domain.confirmations import ActivityLabelConfirmation
from mosemo.labeling.infrastructure.tables import activity_label_confirmations


class ConfirmationRepository:
    def add(
        self, db_session: AsyncSession, confirmation: ActivityLabelConfirmation
    ) -> None:
        db_session.add(confirmation)

    async def find(
        self, db_session: AsyncSession, *, account_id: UUID, first_event_id: UUID
    ) -> ActivityLabelConfirmation | None:
        return await db_session.scalar(
            select(ActivityLabelConfirmation).where(
                activity_label_confirmations.c.account_id == account_id,
                activity_label_confirmations.c.first_event_id == first_event_id,
            )
        )

    async def list_by_first_events(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        first_event_ids: Collection[UUID],
    ) -> list[ActivityLabelConfirmation]:
        if not first_event_ids:
            return []
        result = await db_session.scalars(
            select(ActivityLabelConfirmation).where(
                activity_label_confirmations.c.account_id == account_id,
                activity_label_confirmations.c.first_event_id.in_(first_event_ids),
            )
        )
        return list(result.all())

    async def list_example_page(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        exclude_first_event_id: UUID,
        limit: int,
        offset: int,
    ) -> list[ActivityLabelConfirmation]:
        """The account's confirmations, most recently updated first."""
        result = await db_session.scalars(
            select(ActivityLabelConfirmation)
            .where(
                activity_label_confirmations.c.account_id == account_id,
                activity_label_confirmations.c.first_event_id != exclude_first_event_id,
            )
            .order_by(
                activity_label_confirmations.c.updated_at.desc(),
                activity_label_confirmations.c.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list(result.all())

    async def list_by_keys(
        self, db_session: AsyncSession, keys: list[tuple[UUID, UUID]]
    ) -> list[ActivityLabelConfirmation]:
        """Confirmations by `(account_id, first_event_id)` across accounts."""
        result = await db_session.scalars(
            select(ActivityLabelConfirmation).where(
                tuple_(
                    activity_label_confirmations.c.account_id,
                    activity_label_confirmations.c.first_event_id,
                ).in_(keys)
            )
        )
        return list(result.all())
