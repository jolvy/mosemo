from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activity_labels.confirmations.models import ActivityLabelConfirmation


class ConfirmationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
