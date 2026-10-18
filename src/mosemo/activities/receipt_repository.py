"""Batch receipt persistence within the caller's processing transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.models import ActivityBatchReceipt


class BatchReceiptRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def has_for_account(self, account_id: UUID) -> bool:
        return (
            await self._session.scalar(
                select(ActivityBatchReceipt.batch_id)
                .where(ActivityBatchReceipt.account_id == account_id)
                .limit(1)
            )
        ) is not None

    async def claim(
        self, *, account_id: UUID, device_id: UUID, batch_id: UUID, fingerprint: str
    ) -> ActivityBatchReceipt | None:
        return await self._session.scalar(
            insert(ActivityBatchReceipt)
            .values(
                account_id=account_id,
                device_id=device_id,
                batch_id=batch_id,
                fingerprint=fingerprint,
                results=[],
            )
            .on_conflict_do_nothing()
            .returning(ActivityBatchReceipt)
        )

    async def find(
        self, *, account_id: UUID, device_id: UUID, batch_id: UUID
    ) -> ActivityBatchReceipt | None:
        return await self._session.get(
            ActivityBatchReceipt, (account_id, device_id, batch_id)
        )
