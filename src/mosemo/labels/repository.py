from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.labels.models import (
    DEFAULT_LABEL_NAMES,
    ActivityLabelConfirmation,
    ActivityLabelProposal,
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

    async def list_active_owned(self, *, account_id: UUID) -> list[Label]:
        result = await self._session.scalars(
            select(Label)
            .where(Label.account_id == account_id)
            .where(Label.archived_at.is_(None))
            .order_by(Label.display_name, Label.label_id)
        )
        return list(result.all())

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

    async def find_proposal(
        self,
        *,
        account_id: UUID,
        first_event_id: UUID,
        segment_version: str,
    ) -> ActivityLabelProposal | None:
        return await self._session.scalar(
            select(ActivityLabelProposal)
            .where(ActivityLabelProposal.account_id == account_id)
            .where(ActivityLabelProposal.first_event_id == first_event_id)
            .where(ActivityLabelProposal.segment_version == segment_version)
        )

    async def list_confirmations_with_label_names(
        self,
        *,
        account_id: UUID,
        first_event_ids: set[UUID],
    ) -> dict[UUID, tuple[ActivityLabelConfirmation, str | None]]:
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
            confirmation.first_event_id: (confirmation, display_name)
            for confirmation, display_name in rows
        }
