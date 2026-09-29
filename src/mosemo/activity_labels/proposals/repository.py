from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activity_labels.proposals.models import ActivityLabelProposal


class ProposalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
