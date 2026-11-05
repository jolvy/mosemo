"""Connect raw subscribed messages to activity batch processing."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.identity import bootstrap as identity_bootstrap
from mosemo.labels import bootstrap as labels_bootstrap
from mosemo.observation import bootstrap as observation_bootstrap
from mosemo.observation.bootstrap import (
    ActivityRegistrationRequested,
    ProcessActivityBatch,
)
from mosemo.platform.clock import utc_now
from mosemo.platform.config import BatchDeliveryConfig
from mosemo.platform.messaging.runtime import consumer_broker
from mosemo.platform.messaging.subscriber import (
    MessageConsumer,
    MessageProcessor,
    RawMessage,
)
from mosemo.platform.providers import get_event_dispatcher


class InvalidBatchMessageError(ValueError):
    """Safe diagnostic only; do not expose message contents."""


def decode_batch(
    key: bytes | None, value: bytes | None
) -> ActivityRegistrationRequested:
    try:
        message = ActivityRegistrationRequested.model_validate_json(value or b"")
        if key != str(message.account_id).encode("utf-8"):
            raise ValueError("account key mismatch")
    except ValueError:
        raise InvalidBatchMessageError("invalid_activity_batch_message") from None
    return message


class BatchProcessingExecutor:
    """Processes each decoded message in a fresh session.

    The observation use case opens and commits the message's transaction.
    """

    def __init__(
        self,
        sessions: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        *,
        process_batch: ProcessActivityBatch,
    ) -> None:
        self.sessions = sessions
        self._process_batch = process_batch

    async def execute(self, message: ActivityRegistrationRequested) -> None:
        batch = message.to_activity_batch()
        async with self.sessions() as db_session:
            await self._process_batch.execute(
                db_session, account_id=message.account_id, batch=batch
            )


def activity_batch_handler(executor: BatchProcessingExecutor) -> MessageProcessor:
    async def process(message: RawMessage) -> None:
        await executor.execute(decode_batch(message.key, message.value))

    return process


async def run_consumer(
    config: BatchDeliveryConfig,
    sessions: async_sessionmaker[AsyncSession],
    *,
    stop: asyncio.Event,
) -> None:
    if not config.enabled:
        raise ValueError("Activity Consumer requires BATCH_ENABLED=true")
    identity_bootstrap.map_tables()
    labels_bootstrap.map_tables()
    observation_bootstrap.map_tables()
    executor = BatchProcessingExecutor(
        sessions,
        process_batch=observation_bootstrap.create_process_activity_batch(
            devices=identity_bootstrap.create_device_directory(),
            dispatcher=get_event_dispatcher(),
            clock=utc_now,
        ),
    )
    await MessageConsumer(
        consumer_broker(config.bootstrap_servers),
        activity_batch_handler(executor),
    ).run(config.topic, stop, group_id=config.group_id)
