"""Development-only volatile delivery, NOT Kafka ACK/parking/offset semantics."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4

from faststream.kafka import KafkaBroker
from sqlalchemy.exc import DBAPIError, OperationalError

from mosemo.activities.messages import ActivityRegistrationRequested, TerminalBatchError


class DeliveryUnavailable(Exception):
    pass


class RetainedMessageState(StrEnum):
    QUEUED = "queued"
    ACTIVE = "active"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(slots=True)
class RetainedMessage:
    message: ActivityRegistrationRequested
    state: RetainedMessageState = RetainedMessageState.QUEUED
    attempts: int = 0
    error_code: str | None = None


class BatchDelivery:
    def __init__(
        self,
        broker: KafkaBroker,
        *,
        topic: str,
        capacity: int = 64,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.broker = broker
        self.topic = topic
        self.capacity = capacity
        self._sleep = sleep
        self._jitter = jitter
        self._queue: asyncio.Queue[UUID] = asyncio.Queue(maxsize=capacity)
        self._retained: dict[UUID, RetainedMessage] = {}
        self.worker: asyncio.Task[None] | None = None
        self.accepting = False
        self.fatal_error_code: str | None = None

    def admit(self, message: ActivityRegistrationRequested) -> UUID:
        if (
            not self.accepting
            or self.worker is None
            or self.worker.done()
            or len(self._retained) >= self.capacity
        ):
            raise DeliveryUnavailable
        identifier = uuid4()
        self._queue.put_nowait(identifier)
        self._retained[identifier] = RetainedMessage(message)
        return identifier

    def inspect(self) -> dict[UUID, RetainedMessage]:
        # Explicit developer function. Payload is never exposed through HTTP/logs.
        return {
            key: RetainedMessage(
                value.message, value.state, value.attempts, value.error_code
            )
            for key, value in self._retained.items()
        }

    def reprocess(self, identifier: UUID) -> None:
        value = self._retained[identifier]
        if (
            value.state is not RetainedMessageState.FAILED
            or not self.accepting
            or self.worker is None
            or self.worker.done()
        ):
            raise DeliveryUnavailable
        # Worker processes this failed head before its blocked successors.
        value.state = RetainedMessageState.QUEUED
        value.attempts = 0
        value.error_code = None
        self._queue.put_nowait(identifier)

    @asynccontextmanager
    async def lifespan(self):
        self.worker = asyncio.create_task(self._run(), name="activity-batch-worker")
        self.worker.add_done_callback(self._observe_worker)
        self.accepting = True
        try:
            yield
        finally:
            self.accepting = False
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
            except Exception as error:  # noqa: BLE001 - Observe fatal error without source logging.
                self.fatal_error_code = type(error).__name__

    def _observe_worker(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self.fatal_error_code = type(error).__name__
                self.accepting = False

    async def wait_idle(self) -> None:
        """Developer/test rendezvous, never called by the admission route."""
        await self._queue.join()

    async def _publish(self, value: RetainedMessage) -> bool:
        for attempt in range(5):
            value.attempts += 1
            try:
                await self.broker.publish(
                    {"source": value.message.body.decode("utf-8")},
                    self.topic,
                    headers={
                        "content-type": "application/json",
                        "x-account-id": str(value.message.account_id),
                    },
                )
                return True
            except Exception as error:  # noqa: BLE001 - Observe subscriber failures without logging source.
                # Only known transient DB/transport failures are retried.
                transient = isinstance(
                    error, (OperationalError, ConnectionError, TimeoutError)
                ) or (
                    isinstance(error, DBAPIError)
                    and (
                        error.connection_invalidated
                        or getattr(error.orig, "sqlstate", None)
                        in {"40001", "40P01", "55P03"}
                    )
                )
                if transient and attempt < 4:
                    await self._sleep((2**attempt) * self._jitter())
                    continue
                value.state = RetainedMessageState.FAILED
                value.error_code = (
                    error.args[0]
                    if isinstance(error, TerminalBatchError)
                    else type(error).__name__
                )
                return False
        return False

    async def _run(self) -> None:
        while True:
            identifier = await self._queue.get()
            try:
                value = self._retained[identifier]
                device = (value.message.account_id, value.message.device_id)
                for key, predecessor in self._retained.items():
                    if key == identifier:
                        break
                    if (
                        predecessor.message.account_id,
                        predecessor.message.device_id,
                    ) == device:
                        value.state = RetainedMessageState.BLOCKED
                        break
                if value.state is RetainedMessageState.BLOCKED:
                    continue
                value.state = RetainedMessageState.ACTIVE
                if await self._publish(value):
                    del self._retained[identifier]
                    # Release successors in original admission order, without new tasks.
                    for key, successor in self._retained.items():
                        if (
                            successor.message.account_id,
                            successor.message.device_id,
                        ) == device and successor.state is RetainedMessageState.BLOCKED:
                            successor.state = RetainedMessageState.QUEUED
                            self._queue.put_nowait(key)
                            break
            except asyncio.CancelledError:
                value.state = RetainedMessageState.FAILED
                value.error_code = "shutdown_cancelled"
                raise
            except Exception as error:
                value.state = RetainedMessageState.FAILED
                value.error_code = type(error).__name__
                raise
            finally:
                self._queue.task_done()
