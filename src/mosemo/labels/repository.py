from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.labels.models import (
    DEFAULT_LABEL_NAMES,
    ActivityLabelConfirmation,
    Label,
)


class LabelRepository:
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
