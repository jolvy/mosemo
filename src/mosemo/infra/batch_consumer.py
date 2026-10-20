"""Supervise registered FastStream execution and partition ownership cleanup."""

import asyncio
from collections.abc import Iterable

from faststream import BaseMiddleware
from faststream.kafka import KafkaBroker, KafkaMessage

from mosemo.activities.executor import BatchProcessingExecutor
from mosemo.activities.handler import register_activity_handler
from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.infra.kafka_compat import BatchPartition, BatchRebalanceAdapter


class InvalidBatchMessage(ValueError):
    """Safe diagnostic only; do not expose message contents."""


def decode_batch(
    key: bytes | None, value: bytes | None
) -> ActivityRegistrationRequested:
    try:
        message = ActivityRegistrationRequested.model_validate_json(value or b"")
        if key != str(message.account_id).encode("utf-8"):
            raise ValueError("account key mismatch")
    except ValueError:
        raise InvalidBatchMessage("invalid_activity_batch_message") from None
    return message


class ActivityBatchConsumer:
    def __init__(
        self,
        broker: KafkaBroker,
        executor: BatchProcessingExecutor,
        *,
        shutdown_timeout: float = 30.0,
    ):
        self.broker = broker
        self.executor = executor
        self.shutdown_timeout = shutdown_timeout
        self._closing = False
        self._stop: asyncio.Event | None = None
        self._executions: dict[BatchPartition, asyncio.Task[None]] = {}
        self._revoked: set[asyncio.Task[None]] = set()
        self._failure: asyncio.Future[None] | None = None

    def _fail(self, error: Exception) -> None:
        self._closing = True
        if self._failure is not None and not self._failure.done():
            self._failure.set_exception(error)

    async def run(self, topic: str, stop: asyncio.Event, *, group_id: str) -> None:
        self._stop = stop
        self._failure = asyncio.get_running_loop().create_future()
        owner = self

        class ObserveErrors(BaseMiddleware):
            async def after_processed(self, exc_type=None, exc_val=None, exc_tb=None):
                # Also observe parser/decoder failures outside the handler call.
                if isinstance(exc_val, Exception):
                    owner._fail(exc_val)
                return False

        self.broker.add_middleware(ObserveErrors)
        register_activity_handler(
            self.broker,
            self.handle,
            topic=topic,
            group_id=group_id,
            listener=BatchRebalanceAdapter(self),
        )
        stopped = asyncio.create_task(stop.wait())
        startup = asyncio.create_task(self.broker.start())
        try:
            await asyncio.wait(
                [startup, stopped, self._failure], return_when=asyncio.FIRST_COMPLETED
            )
            if startup.done():
                startup.result()
            if not stop.is_set():
                if self._failure.done():
                    self._failure.result()
                await startup
                await asyncio.wait(
                    [stopped, self._failure], return_when=asyncio.FIRST_COMPLETED
                )
                if self._failure.done():
                    self._failure.result()
        finally:
            self._closing = True
            stopped.cancel()
            startup.cancel()
            await asyncio.gather(stopped, startup, return_exceptions=True)
            try:
                await self._drain()
            finally:
                await self.broker.stop()
                if self._failure.done() and not self._failure.cancelled():
                    self._failure.exception()
                self._failure.cancel()
        if not self._failure.cancelled():
            self._failure.result()

    async def handle(self, message: KafkaMessage) -> None:
        if self._closing or (self._stop and self._stop.is_set()):
            return
        raw = message.raw_message
        assert not isinstance(raw, tuple)
        partition = BatchPartition(raw.topic, raw.partition)
        # A revocation cancels this attempt without killing FastStream's
        # subscription loop, which must continue after reassignment.
        task = asyncio.create_task(self._execute(message))
        self._executions[partition] = task
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            if task not in self._revoked and not self._closing:
                self._fail(RuntimeError("activity_batch_execution_cancelled"))
        except Exception as error:  # noqa: BLE001 - propagate through runtime supervisor
            self._fail(error)
        finally:
            self._revoked.discard(task)
            if self._executions.get(partition) is task:
                del self._executions[partition]

    async def _execute(self, message: KafkaMessage) -> None:
        raw = message.raw_message
        assert not isinstance(raw, tuple)
        batch = decode_batch(raw.key, raw.value)
        await self.executor.execute(batch)
        # FastStream manual mode disables auto-commit; each native worker
        # handles one message at a time and ACKs before reading its next one.
        await message.ack()

    async def revoke(self, revoked: Iterable[BatchPartition]) -> None:
        tasks = [self._executions[tp] for tp in revoked if tp in self._executions]
        self._revoked.update(tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _drain(self) -> None:
        tasks = list(self._executions.values())
        if not tasks:
            return
        try:
            await asyncio.wait(tasks, timeout=self.shutdown_timeout)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
