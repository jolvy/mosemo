"""Path-specific Executors own batch transactions; only legacy takes a DB lock."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.batch_processing import ActivityBatchProcessor
from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.activities.schemas import ActivityBatchRequest
from mosemo.activities.service import acquire_activity_timeline_lock


class BatchProcessingExecutor:
    def __init__(
        self, sessions: Callable[[], AbstractAsyncContextManager[AsyncSession]]
    ):
        self.sessions = sessions

    async def execute(self, message: ActivityRegistrationRequested) -> None:
        batch = ActivityBatchRequest.model_validate(
            {
                "batchId": message.batch_id,
                "deviceId": message.device_id,
                "records": message.records,
            }
        )
        async with self.sessions() as session, session.begin():
            await ActivityBatchProcessor(session).process(message.account_id, batch)


class LegacyBatchExecutor:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def execute(
        self, account_id: UUID, batch: ActivityBatchRequest
    ) -> list[dict[str, str]]:
        async with self.session.begin():
            await acquire_activity_timeline_lock(self.session, account_id=account_id)
            return await ActivityBatchProcessor(self.session).process(account_id, batch)


class BatchFailureHandler:
    """Future failure boundary. No storage, blocking, retry or ACK policy exists."""

    async def handle(self) -> None:
        raise NotImplementedError("batch failure policy is not implemented")


class BatchRecoveryService:
    """Future recovery boundary; never report a successful recovery."""

    async def recover(self) -> None:
        raise NotImplementedError("batch recovery policy is not implemented")
