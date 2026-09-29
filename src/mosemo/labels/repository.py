from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activity_labels.catalog.models import Label
from mosemo.labels.models import ActivityLabelConfirmation


@dataclass(frozen=True, slots=True)
class ConfirmationWithLabelName:
    confirmation: ActivityLabelConfirmation
    display_name: str | None


class LabelRepository:
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

    async def list_confirmations_with_label_names(
        self,
        *,
        account_id: UUID,
        first_event_ids: set[UUID],
    ) -> dict[UUID, ConfirmationWithLabelName]:
        if not first_event_ids:
            return {}

        statement = (
            select(ActivityLabelConfirmation, Label.display_name)
            .outerjoin(
                Label,
                (Label.label_id == ActivityLabelConfirmation.label_id)
                & (Label.account_id == account_id),
            )
            .where(ActivityLabelConfirmation.account_id == account_id)
            .where(ActivityLabelConfirmation.first_event_id.in_(first_event_ids))
        )
        rows = (await self._session.execute(statement)).all()
        return {
            confirmation.first_event_id: ConfirmationWithLabelName(
                confirmation=confirmation,
                display_name=display_name,
            )
            for confirmation, display_name in rows
        }
