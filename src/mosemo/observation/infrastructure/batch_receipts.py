"""Batch receipt persistence within the caller's processing transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.observation.domain.activities import ActivityBatchReceipt
from mosemo.observation.infrastructure.tables import activity_batch_receipts


class BatchReceiptRepository:
    async def has_for_account(self, db_session: AsyncSession, account_id: UUID) -> bool:
        return (
            await db_session.scalar(
                select(activity_batch_receipts.c.batch_id)
                .where(activity_batch_receipts.c.account_id == account_id)
                .limit(1)
            )
        ) is not None

    async def claim(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        device_id: UUID,
        batch_id: UUID,
        fingerprint: str,
    ) -> ActivityBatchReceipt | None:
        return await db_session.scalar(
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
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        device_id: UUID,
        batch_id: UUID,
    ) -> ActivityBatchReceipt | None:
        return await db_session.get(
            ActivityBatchReceipt, (account_id, device_id, batch_id)
        )
