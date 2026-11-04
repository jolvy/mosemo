from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activity_labels.confirmations.models import ActivityLabelConfirmation
from mosemo.labels.public import LabelCatalog


@dataclass(frozen=True, slots=True)
class ConfirmationWithLabelName:
    confirmation: ActivityLabelConfirmation
    display_name: str | None


class LabelQueryRepository:
    def __init__(self, session: AsyncSession, *, labels: LabelCatalog) -> None:
        self._session = session
        self._labels = labels

    async def list_confirmations_with_label_names(
        self,
        *,
        account_id: UUID,
        first_event_ids: set[UUID],
    ) -> dict[UUID, ConfirmationWithLabelName]:
        """Confirmations with the account's label names from the label catalog.

        A missing label leaves its name `None`, as the outer join it replaces did.
        """
        if not first_event_ids:
            return {}

        confirmations = (
            await self._session.scalars(
                select(ActivityLabelConfirmation)
                .where(ActivityLabelConfirmation.account_id == account_id)
                .where(ActivityLabelConfirmation.first_event_id.in_(first_event_ids))
            )
        ).all()
        names = await self._labels.find_names(
            self._session,
            account_id=account_id,
            label_ids={
                confirmation.label_id
                for confirmation in confirmations
                if confirmation.label_id is not None
            },
        )
        return {
            confirmation.first_event_id: ConfirmationWithLabelName(
                confirmation=confirmation,
                display_name=(
                    None
                    if confirmation.label_id is None
                    else names.get(confirmation.label_id)
                ),
            )
            for confirmation in confirmations
        }
