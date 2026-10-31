from uuid import UUID

from sqlalchemy import and_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activity_labels.confirmations.models import ActivityLabelConfirmation


class ConfirmationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, confirmation: ActivityLabelConfirmation) -> None:
        self._session.add(confirmation)

    async def find_confirmation(
        self,
        *,
        account_id: UUID,
        first_event_id: UUID,
    ) -> ActivityLabelConfirmation | None:
        return await self._session.scalar(
            select(ActivityLabelConfirmation)
            .where(ActivityLabelConfirmation.account_id == account_id)
            .where(ActivityLabelConfirmation.first_event_id == first_event_id)
        )

    async def list_example_page(
        self,
        *,
        account_id: UUID,
        exclude_first_event_id: UUID,
        limit: int,
        offset: int,
    ) -> list[tuple[ActivityLabelConfirmation, ActivityTimelineSegment]]:
        query = (
            select(ActivityLabelConfirmation, ActivityTimelineSegment)
            .join(
                ActivityTimelineSegment,
                and_(
                    ActivityTimelineSegment.account_id
                    == ActivityLabelConfirmation.account_id,
                    ActivityTimelineSegment.first_event_id
                    == ActivityLabelConfirmation.first_event_id,
                ),
            )
            .where(ActivityLabelConfirmation.account_id == account_id)
            .where(ActivityLabelConfirmation.first_event_id != exclude_first_event_id)
            .order_by(
                ActivityLabelConfirmation.updated_at.desc(),
                ActivityLabelConfirmation.confirmation_id.desc(),
            )
        )
        rows = (await self._session.execute(query.limit(limit).offset(offset))).all()
        return [(confirmation, segment) for confirmation, segment in rows]

    async def list_by_keys(
        self, keys: list[tuple[UUID, UUID]]
    ) -> list[ActivityLabelConfirmation]:
        result = await self._session.scalars(
            select(ActivityLabelConfirmation).where(
                tuple_(
                    ActivityLabelConfirmation.account_id,
                    ActivityLabelConfirmation.first_event_id,
                ).in_(keys)
            )
        )
        return list(result.all())
