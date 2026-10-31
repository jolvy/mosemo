"""Path-specific Executors own batch transactions; only legacy takes a DB lock."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.batch_processing import ActivityBatchProcessor
from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.schemas import ActivityBatchRequest
from mosemo.clock import Clock


class BatchProcessingExecutor:
    def __init__(
        self,
        sessions: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        *,
        clock: Clock,
    ):
        self.sessions = sessions
        self._clock = clock

    async def execute(self, message: ActivityRegistrationRequested) -> None:
        batch = ActivityBatchRequest.model_validate(
            {
                "batchId": message.batch_id,
                "deviceId": message.device_id,
                "records": message.records,
            }
        )
        async with self.sessions() as session, session.begin():
            await ActivityBatchProcessor(session, clock=self._clock).process(
                message.account_id, batch
            )


class LegacyBatchExecutor:
    def __init__(self, session: AsyncSession, *, clock: Clock):
        self.session = session
        self._clock = clock

    async def execute(
        self, account_id: UUID, batch: ActivityBatchRequest
    ) -> list[dict[str, str]]:
        async with self.session.begin():
            await ActivityRepository(self.session).lock_timeline(account_id)
            return await ActivityBatchProcessor(
                self.session, clock=self._clock
            ).process(account_id, batch)


class BatchFailureHandler:
    """Future failure boundary. No storage, blocking, retry or ACK policy exists."""

    async def handle(self) -> None:
        raise NotImplementedError("batch failure policy is not implemented")


class BatchRecoveryService:
    """Future recovery boundary; never report a successful recovery."""

    async def recover(self) -> None:
        raise NotImplementedError("batch recovery policy is not implemented")
