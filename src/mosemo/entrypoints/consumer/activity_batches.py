"""Connect raw subscribed messages to activity batch processing."""

import asyncio
import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager

from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.analytics import bootstrap as analytics_bootstrap
from mosemo.identity import bootstrap as identity_bootstrap
from mosemo.labels import bootstrap as labels_bootstrap
from mosemo.observation import bootstrap as observation_bootstrap
from mosemo.observation.bootstrap import (
    ActivityRegistrationRequested,
    ProcessActivityBatch,
    TerminalBatchError,
)
from mosemo.observation.public import ActivityTimelineBusyError
from mosemo.platform.clock import utc_now
from mosemo.platform.messaging.config import BatchDeliveryConfig
from mosemo.platform.messaging.runtime import consumer_broker
from mosemo.platform.messaging.subscriber import (
    MessageConsumer,
    MessageProcessor,
    RawMessage,
)
from mosemo.platform.providers import get_event_dispatcher
from mosemo.shared.application.events import EventDispatcher

logger = logging.getLogger(__name__)

# Failures another attempt of the same message can overcome; a batch whose
# processing fails for any other reason is skipped (ADR 0006).
TRANSIENT_FAILURES = (
    ActivityTimelineBusyError,
    DBAPIError,
    PoolTimeoutError,
    OSError,
)
# Waits between the five attempts of a message (requirement FR-15).
RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0)
OPERATION = "activity_batch_consume"


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


def register_analytics_handlers(dispatcher: EventDispatcher) -> None:
    """Subscribe analytics with collaborators built for this process."""
    analytics_bootstrap.register_handlers(
        dispatcher,
        timeline=observation_bootstrap.create_observation_timeline(),
        accounts=identity_bootstrap.create_account_directory(),
        focus_sessions=observation_bootstrap.create_focus_session_log(),
        clock=utc_now,
    )


def _position(message: RawMessage) -> dict[str, object]:
    return {
        "operation": OPERATION,
        "topic": message.topic,
        "partition": message.partition,
        "offset": message.offset,
    }


def _cancelling() -> bool:
    # A revocation or shutdown cancellation can turn into another exception
    # while the session closes; that message must stay unacknowledged.
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


async def _execute_with_retries(
    executor: BatchProcessingExecutor,
    message: RawMessage,
    *,
    retry_delays: Sequence[float],
) -> None:
    batch = decode_batch(message.key, message.value)
    attempts = len(retry_delays) + 1
    for attempt in range(1, attempts + 1):
        try:
            await executor.execute(batch)
        except TRANSIENT_FAILURES as error:
            if _cancelling():
                raise
            fields = {**_position(message), "error_code": type(error).__name__}
            if attempt == attempts:
                # Ends the Consumer without ACK; this position resumes it.
                logger.error(
                    "activity batch failed",
                    extra={**fields, "outcome": "failed", "retryable": False},
                )
                raise
            logger.warning(
                "activity batch retrying",
                extra={
                    **fields,
                    "outcome": "retrying",
                    "retryable": True,
                    "attempt": attempt,
                },
            )
        else:
            return
        await asyncio.sleep(retry_delays[attempt - 1])


def activity_batch_handler(
    executor: BatchProcessingExecutor,
    *,
    retry_delays: Sequence[float] = RETRY_DELAYS,
) -> MessageProcessor:
    """Return normally to ACK a processed or skipped message; raise to stop."""

    async def process(message: RawMessage) -> None:
        skipped = {**_position(message), "outcome": "skipped", "retryable": False}
        try:
            await _execute_with_retries(executor, message, retry_delays=retry_delays)
        except (InvalidBatchMessageError, TerminalBatchError) as error:
            if _cancelling():
                raise
            logger.warning(
                "activity batch skipped", extra={**skipped, "error_code": str(error)}
            )
        except TRANSIENT_FAILURES:
            raise
        except Exception as error:  # skip one message without stopping the Consumer
            if _cancelling():
                raise
            # The formatter keeps the type and last frame, never the message.
            logger.exception(
                "activity batch skipped",
                extra={**skipped, "error_code": type(error).__name__},
            )

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
    analytics_bootstrap.map_tables()
    register_analytics_handlers(get_event_dispatcher())
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
