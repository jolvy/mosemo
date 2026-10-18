"""One processing attempt owns one session; the Processor owns its transaction."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.batch_processing import ActivityBatchProcessor
from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.activities.schemas import ActivityBatchRequest


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
        async with self.sessions() as session:
            await ActivityBatchProcessor(session).process(message.account_id, batch)


class BatchFailureHandler:
    """Future failure boundary. No storage, blocking, retry or ACK policy exists."""

    async def handle(self) -> None:
        raise NotImplementedError("batch failure policy is not implemented")


class BatchRecoveryService:
    """Future recovery boundary; never report a successful recovery."""

    async def recover(self) -> None:
        raise NotImplementedError("batch recovery policy is not implemented")
