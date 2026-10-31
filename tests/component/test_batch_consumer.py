import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from faststream import AckPolicy
from faststream.kafka import KafkaBroker

from mosemo.activities.executor import BatchProcessingExecutor
from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.activities.schemas import ActivityBatchRequest
from mosemo.clock import utc_now
from mosemo.infra.batch_consumer import (
    ActivityBatchConsumer,
    InvalidBatchMessage,
    decode_batch,
)
from mosemo.infra.batch_runtime import MessagingRuntime
from mosemo.infra.kafka_compat import BatchPartition
from tests.clock_support import FakeClock
from tests.component.test_activities_api import activity_request


def message():
    return ActivityRegistrationRequested.from_batch(
        uuid4(), ActivityBatchRequest.model_validate(activity_request())
    )


@pytest.mark.parametrize("invalid", ["missing", "mismatch", "json", "schema"])
def test_invalid_message_never_reaches_processing(invalid):
    msg = message()
    key, value = (
        str(msg.account_id).encode(),
        msg.model_dump_json(by_alias=True).encode(),
    )
    if invalid == "missing":
        key = None
    elif invalid == "mismatch":
        key = str(uuid4()).encode()
    elif invalid == "json":
        value = b"private-invalid-json"
    else:
        value = b'{"private-field": "sensitive"}'
    with pytest.raises(InvalidBatchMessage, match="^invalid_activity_batch_message$"):
        decode_batch(key, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "error", "cancel"])
async def test_executor_owns_fresh_session_for_each_attempt(monkeypatch, failure):
    opened, closed, used = [], [], []

    @asynccontextmanager
    async def sessions():
        session = Mock()
        session.begin.return_value = AsyncMock()
        opened.append(session)
        try:
            yield session
        finally:
            closed.append(session)

    async def process(self, owner, batch):
        used.append(self.session)
        if failure == "error":
            raise RuntimeError("failed")
        if failure == "cancel":
            raise asyncio.CancelledError

    monkeypatch.setattr(
        "mosemo.activities.executor.ActivityBatchProcessor.process", process
    )
    executor = BatchProcessingExecutor(sessions, clock=FakeClock(utc_now()))
    for _ in range(2):
        if failure:
            with pytest.raises(
                RuntimeError if failure == "error" else asyncio.CancelledError
            ):
                await executor.execute(message())
        else:
            await executor.execute(message())
    assert opened == used == closed
    assert opened[0] is not opened[1]


class Broker:
    """Lifecycle double; real subscriber execution is covered with Kafka below."""

    def __init__(self):
        self.queues = {BatchPartition("test", p): asyncio.Queue() for p in range(2)}
        self.commit = AsyncMock()
        self.stop = AsyncMock(side_effect=self.close)
        self.tasks = []
        self.handler = AsyncMock()

    def add_middleware(self, middleware):
        pass

    def subscriber(self, *topics, **options):
        self.options = options
        self.listener = options["listener"]

        def register(handler):
            self.handler = handler
            return handler

        return register

    async def start(self):
        self.tasks = [asyncio.create_task(self.deliver(tp)) for tp in self.queues]

    async def deliver(self, tp):
        while True:
            raw = await self.queues[tp].get()
            await self.handler(SimpleNamespace(raw_message=raw, ack=self.commit))

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def put(self, tp, offset):
        msg = message()
        self.queues[tp].put_nowait(
            SimpleNamespace(
                topic=tp.topic,
                partition=tp.partition,
                key=str(msg.account_id).encode(),
                value=msg.model_dump_json(by_alias=True).encode(),
                offset=offset,
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_shutdown_drains_only_inflight_and_cancels_after_deadline(timeout):
    broker = Broker()
    entered = asyncio.Queue()
    release = asyncio.Event()
    cleaned = []

    async def execute(msg):
        await entered.put(msg)
        try:
            await release.wait()
        finally:
            cleaned.append(msg)

    executor = AsyncMock(execute=execute)
    worker = ActivityBatchConsumer(
        cast(KafkaBroker, broker),
        executor,
        shutdown_timeout=0.02 if timeout else 1,
    )
    stop = asyncio.Event()
    pending = asyncio.create_task(worker.run("test", stop, group_id="group"))
    try:
        for tp in broker.queues:
            broker.put(tp, 0)
            broker.put(tp, 1)
        for _ in range(2):
            try:
                await asyncio.wait_for(entered.get(), 1)
            except TimeoutError:
                if pending.done():
                    pending.result()
                raise
        stop.set()
        # Wake the runtime; in-flight tasks remain held until release/deadline.
        await asyncio.sleep(0)
        if not timeout:
            release.set()
        await asyncio.wait_for(pending, 2)
        assert len(cleaned) == 2
        assert entered.empty()  # prefetched messages never start new DB attempts
        assert broker.commit.await_count == (0 if timeout else 2)
        broker.stop.assert_awaited_once()
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_revoke_cancels_session_and_does_not_commit_unprocessed_offset():
    broker = Broker()
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def execute(msg):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    worker = ActivityBatchConsumer(
        cast(KafkaBroker, broker),
        AsyncMock(execute=execute),
        shutdown_timeout=0.01,
    )
    stop = asyncio.Event()
    pending = asyncio.create_task(worker.run("test", stop, group_id="group"))
    tp = next(iter(broker.queues))
    try:
        broker.put(tp, 0)
        await asyncio.wait_for(entered.wait(), 1)
        await broker.listener.on_partitions_revoked([tp])
        assert cleaned.is_set()
        broker.commit.assert_not_called()
    finally:
        stop.set()
        await asyncio.wait_for(pending, 1)


@pytest.mark.asyncio
async def test_processing_exception_surfaces_without_ack_or_fictitious_recovery():
    broker = Broker()
    executor = AsyncMock()
    executor.execute.side_effect = RuntimeError("failed")
    worker = ActivityBatchConsumer(
        cast(KafkaBroker, broker), executor, shutdown_timeout=0.01
    )
    broker.put(next(iter(broker.queues)), 0)
    with pytest.raises(RuntimeError, match="failed"):
        await asyncio.wait_for(worker.run("test", asyncio.Event(), group_id="group"), 1)
    broker.commit.assert_not_called()
    broker.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_execution_cancellation_cannot_skip_to_a_later_ack():
    broker = Broker()
    executor = AsyncMock()
    executor.execute.side_effect = asyncio.CancelledError
    worker = ActivityBatchConsumer(cast(KafkaBroker, broker), executor)
    partition = next(iter(broker.queues))
    broker.put(partition, 0)
    broker.put(partition, 1)
    with pytest.raises(RuntimeError, match="activity_batch_execution_cancelled"):
        await asyncio.wait_for(worker.run("test", asyncio.Event(), group_id="group"), 1)
    assert executor.execute.await_count == 1
    broker.commit.assert_not_called()
    broker.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_refuses_to_consume_when_delivery_is_disabled(
    config, monkeypatch
):
    config.batch_delivery.enabled = False
    constructor = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr("mosemo.infra.batch_runtime.KafkaBroker", constructor)
    with pytest.raises(ValueError, match="requires BATCH_ENABLED=true"):
        await MessagingRuntime.run_consumer(
            config.batch_delivery, AsyncMock(), asyncio.Event()
        )


@pytest.mark.asyncio
async def test_runtime_configures_manual_offsets_and_earliest(config, monkeypatch):
    broker = Broker()
    constructor = Mock(return_value=broker)
    monkeypatch.setattr("mosemo.infra.batch_runtime.KafkaBroker", constructor)
    stop = asyncio.Event()
    stop.set()
    await MessagingRuntime.run_consumer(config.batch_delivery, AsyncMock(), stop)
    constructor.assert_called_once_with(
        config.batch_delivery.bootstrap_servers,
        logger=None,
        graceful_timeout=0,
        consumer_only=True,
    )
    broker.stop.assert_awaited_once()
    assert broker.options["group_id"] == config.batch_delivery.group_id
    assert broker.options["ack_policy"] is AckPolicy.MANUAL
    assert broker.options["auto_offset_reset"] == "earliest"
    assert broker.options["max_workers"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["startup", "cancel"])
async def test_consumer_startup_failure_or_cancellation_always_closes_client(
    failure, monkeypatch
):
    broker = Broker()
    entered = asyncio.Event()

    async def start():
        entered.set()
        if failure == "startup":
            raise ConnectionError("unavailable")
        await asyncio.Event().wait()

    monkeypatch.setattr(broker, "start", start)
    worker = ActivityBatchConsumer(cast(KafkaBroker, broker), AsyncMock())
    assert worker.shutdown_timeout == 30.0
    pending = asyncio.create_task(worker.run("test", asyncio.Event(), group_id="group"))
    await asyncio.wait_for(entered.wait(), 1)
    if failure == "cancel":
        pending.cancel()
    with pytest.raises(
        ConnectionError if failure == "startup" else asyncio.CancelledError
    ):
        await pending
    broker.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_during_shutdown_drain_is_not_reported_as_success():
    broker = Broker()
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(msg):
        entered.set()
        await release.wait()
        raise RuntimeError("failed during drain")

    worker = ActivityBatchConsumer(
        cast(KafkaBroker, broker),
        AsyncMock(execute=execute),
        shutdown_timeout=0.02,
    )
    stop = asyncio.Event()
    pending = asyncio.create_task(worker.run("test", stop, group_id="group"))
    broker.put(next(iter(broker.queues)), 0)
    await asyncio.wait_for(entered.wait(), 1)
    stop.set()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(RuntimeError, match="failed during drain"):
        await asyncio.wait_for(pending, 1)
    broker.commit.assert_not_called()
    broker.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_during_startup_cancels_connection_attempt_and_closes_client(
    monkeypatch,
):
    broker = Broker()
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def start():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    monkeypatch.setattr(broker, "start", start)
    worker = ActivityBatchConsumer(cast(KafkaBroker, broker), AsyncMock())
    stop = asyncio.Event()
    pending = asyncio.create_task(worker.run("test", stop, group_id="group"))
    await asyncio.wait_for(entered.wait(), 1)
    stop.set()
    await asyncio.wait_for(pending, 1)
    assert cleaned.is_set()
    broker.stop.assert_awaited_once()


def test_cli_initialization_errors_never_expose_credentials():
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    # Override inherited values with deliberately invalid, non-secret fixtures.
    env.update(
        MOSEMO_ENV="test",
        DB_DATABASE="test",
        DB_USER="test",
        DB_HOST="localhost",
        DB_PORT="invalid-port",
        DB_PASSWORD="private-db-marker",
        AUTH_JWT_SECRET_KEY="private-jwt-marker",
    )
    result = subprocess.run(
        [sys.executable, "-m", "mosemo.activities.consumer"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert (
        result.stderr
        == "Activity Consumer failed; exiting without an in-process retry\n"
    )
    assert "private-db-marker" not in result.stderr
    assert "private-jwt-marker" not in result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_fails", [False, True])
async def test_consumer_waits_for_executor_commit_and_never_acks_failed_commit(
    monkeypatch, commit_fails
):
    committing, release, acked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    transaction = AsyncMock()

    async def finish(*args):
        committing.set()
        await release.wait()
        if commit_fails:
            raise RuntimeError("commit failed")

    transaction.__aexit__.side_effect = finish
    session = Mock()
    session.begin.return_value = transaction

    @asynccontextmanager
    async def sessions():
        yield session

    monkeypatch.setattr(
        "mosemo.activities.executor.ActivityBatchProcessor.process", AsyncMock()
    )
    broker = Broker()
    broker.commit.side_effect = lambda: acked.set()
    worker = ActivityBatchConsumer(
        cast(KafkaBroker, broker),
        BatchProcessingExecutor(sessions, clock=FakeClock(utc_now())),
    )
    stop = asyncio.Event()
    pending = asyncio.create_task(worker.run("test", stop, group_id="group"))
    broker.put(next(iter(broker.queues)), 0)
    try:
        await asyncio.wait_for(committing.wait(), 1)
        broker.commit.assert_not_called()
        release.set()
        if commit_fails:
            with pytest.raises(RuntimeError, match="commit failed"):
                await asyncio.wait_for(pending, 1)
            broker.commit.assert_not_called()
        else:
            await asyncio.wait_for(acked.wait(), 1)
            stop.set()
            await asyncio.wait_for(pending, 1)
            broker.commit.assert_awaited_once()
    finally:
        release.set()
        stop.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_revocation_waits_for_target_cleanup_and_leaves_other_partition_running():
    broker = Broker()
    entered = asyncio.Queue()
    cleaning, release_cleanup, release_other = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    attempts = 0

    async def execute(msg):
        nonlocal attempts
        attempt = attempts
        attempts += 1
        await entered.put(attempt)
        if attempt == 0:
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release_cleanup.wait()
        else:
            await release_other.wait()

    worker = ActivityBatchConsumer(
        cast(KafkaBroker, broker), AsyncMock(execute=execute)
    )
    stop = asyncio.Event()
    pending = asyncio.create_task(worker.run("test", stop, group_id="group"))
    partitions = list(broker.queues)
    revoked = None
    try:
        broker.put(partitions[0], 0)
        assert await asyncio.wait_for(entered.get(), 1) == 0
        broker.put(partitions[1], 0)
        assert await asyncio.wait_for(entered.get(), 1) == 1
        revoked = asyncio.create_task(
            broker.listener.on_partitions_revoked(partitions[:1])
        )
        await asyncio.wait_for(cleaning.wait(), 1)
        assert not revoked.done()
        assert not worker._executions[partitions[1]].done()
        broker.commit.assert_not_called()
        release_cleanup.set()
        await asyncio.wait_for(revoked, 1)
        assert not worker._executions[partitions[1]].done()
    finally:
        release_cleanup.set()
        release_other.set()
        stop.set()
        if revoked is not None:
            await asyncio.gather(revoked, return_exceptions=True)
        await asyncio.wait_for(pending, 2)
    broker.commit.assert_awaited_once()
