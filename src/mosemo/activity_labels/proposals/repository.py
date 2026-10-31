from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activity_labels.proposals.models import ActivityLabelProposal


class ProposalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, proposal: ActivityLabelProposal) -> None:
        self._session.add(proposal)

    async def find_by_id(self, proposal_id: UUID) -> ActivityLabelProposal | None:
        return await self._session.get(ActivityLabelProposal, proposal_id)

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

    async def list_by_keys(
        self, keys: list[tuple[UUID, UUID, str]]
    ) -> list[ActivityLabelProposal]:
        result = await self._session.scalars(
            select(ActivityLabelProposal).where(
                tuple_(
                    ActivityLabelProposal.account_id,
                    ActivityLabelProposal.first_event_id,
                    ActivityLabelProposal.segment_version,
                ).in_(keys)
            )
        )
        return list(result.all())
