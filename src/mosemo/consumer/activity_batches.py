"""Connect raw subscribed messages to activity batch processing."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.activities.executor import BatchProcessingExecutor
from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.infra.clock import utc_now
from mosemo.infra.config import BatchDeliveryConfig
from mosemo.infra.messaging.runtime import consumer_broker
from mosemo.infra.messaging.subscriber import (
    MessageConsumer,
    MessageProcessor,
    RawMessage,
)


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
    await MessageConsumer(
        consumer_broker(config.bootstrap_servers),
        activity_batch_handler(BatchProcessingExecutor(sessions, clock=utc_now)),
    ).run(config.topic, stop, group_id=config.group_id)
