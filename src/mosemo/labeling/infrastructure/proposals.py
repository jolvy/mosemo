from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.labeling.domain.proposals import ActivityLabelProposal
from mosemo.labeling.infrastructure.tables import activity_label_proposals


class ProposalRepository:
    def add(self, db_session: AsyncSession, proposal: ActivityLabelProposal) -> None:
        db_session.add(proposal)

    async def find_by_id(
        self, db_session: AsyncSession, proposal_id: UUID
    ) -> ActivityLabelProposal | None:
        return await db_session.get(ActivityLabelProposal, proposal_id)

    async def find(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        first_event_id: UUID,
        segment_version: str,
    ) -> ActivityLabelProposal | None:
        return await db_session.scalar(
            select(ActivityLabelProposal).where(
                activity_label_proposals.c.account_id == account_id,
                activity_label_proposals.c.first_event_id == first_event_id,
                activity_label_proposals.c.segment_version == segment_version,
            )
        )

    async def list_by_keys(
        self, db_session: AsyncSession, keys: list[tuple[UUID, UUID, str]]
    ) -> list[ActivityLabelProposal]:
        """Proposals by `(account_id, first_event_id, segment_version)`."""
        result = await db_session.scalars(
            select(ActivityLabelProposal).where(
                tuple_(
                    activity_label_proposals.c.account_id,
                    activity_label_proposals.c.first_event_id,
                    activity_label_proposals.c.segment_version,
                ).in_(keys)
            )
        )
        return list(result.all())
