import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from faststream import AckPolicy
from faststream.kafka import KafkaBroker
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from mosemo.entrypoints.consumer.activity_batches import (
    InvalidBatchMessageError,
    activity_batch_handler,
    decode_batch,
    run_consumer,
)
from mosemo.observation.bootstrap import TerminalBatchError
from mosemo.observation.presentation.activities.schemas import ActivityBatchRequest
from mosemo.observation.presentation.messages import ActivityRegistrationRequested
from mosemo.observation.public import ActivityTimelineBusyError
from mosemo.platform.clock import utc_now
from mosemo.platform.logging import JsonFormatter
from mosemo.platform.messaging.config import BatchDeliveryConfig
from mosemo.platform.messaging.kafka_compat import BatchPartition
from mosemo.platform.messaging.subscriber import MessageConsumer, RawMessage
from tests.support.clock import FakeClock
from tests.support.observation import activity_request, batch_executor

CONSUMER_LOGGER = "mosemo.entrypoints.consumer.activity_batches"
NO_WAIT = (0, 0, 0, 0)


def session_double() -> Mock:
    """A session that `unit_of_work` can open: no open transaction, no events.

    Its statements, such as the account lock, succeed without a database.
    """
    session = Mock()
    session.in_transaction.return_value = False
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.sync_session.info = {}
    return session


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
    with pytest.raises(
        InvalidBatchMessageError, match="^invalid_activity_batch_message$"
    ):
        decode_batch(key, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "error", "cancel"])
async def test_executor_owns_fresh_session_for_each_attempt(monkeypatch, failure):
    opened, closed, used = [], [], []

    @asynccontextmanager
    async def sessions():
        session = session_double()
        session.begin.return_value = AsyncMock()
        opened.append(session)
        try:
            yield session
        finally:
            closed.append(session)

    async def process(self, db_session, *, account_id, batch):
        used.append(db_session)
        if failure == "error":
            raise RuntimeError("failed")
        if failure == "cancel":
            raise asyncio.CancelledError

    monkeypatch.setattr(
        "mosemo.observation.application.batches.ActivityBatchProcessor.process", process
    )
    executor = batch_executor(sessions, clock=FakeClock(utc_now()))
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
    worker = MessageConsumer(
        cast(KafkaBroker, broker),
        activity_batch_handler(executor),
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

    worker = MessageConsumer(
        cast(KafkaBroker, broker),
        activity_batch_handler(AsyncMock(execute=execute)),
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
    executor.execute.side_effect = OSError("failed")
    worker = MessageConsumer(
        cast(KafkaBroker, broker),
        activity_batch_handler(executor, retry_delays=NO_WAIT),
        shutdown_timeout=0.01,
    )
    broker.put(next(iter(broker.queues)), 0)
    with pytest.raises(OSError, match="failed"):
        await asyncio.wait_for(worker.run("test", asyncio.Event(), group_id="group"), 1)
    assert executor.execute.await_count == 5
    broker.commit.assert_not_called()
    broker.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_execution_cancellation_cannot_skip_to_a_later_ack():
    broker = Broker()
    executor = AsyncMock()
    executor.execute.side_effect = asyncio.CancelledError
    worker = MessageConsumer(
        cast(KafkaBroker, broker), activity_batch_handler(executor)
    )
    partition = next(iter(broker.queues))
    broker.put(partition, 0)
    broker.put(partition, 1)
    with pytest.raises(RuntimeError, match="message_execution_cancelled"):
        await asyncio.wait_for(worker.run("test", asyncio.Event(), group_id="group"), 1)
    assert executor.execute.await_count == 1
    broker.commit.assert_not_called()
    broker.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_refuses_to_consume_when_delivery_is_disabled(monkeypatch):
    batch_delivery = BatchDeliveryConfig(enabled=False)
    constructor = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr("mosemo.platform.messaging.runtime.KafkaBroker", constructor)
    with pytest.raises(ValueError, match="requires BATCH_ENABLED=true"):
        await run_consumer(batch_delivery, AsyncMock(), stop=asyncio.Event())


@pytest.mark.asyncio
async def test_runtime_configures_manual_offsets_and_earliest(monkeypatch):
    batch_delivery = BatchDeliveryConfig()
    broker = Broker()
    constructor = Mock(return_value=broker)
    monkeypatch.setattr("mosemo.platform.messaging.runtime.KafkaBroker", constructor)
    stop = asyncio.Event()
    stop.set()
    await run_consumer(batch_delivery, AsyncMock(), stop=stop)
    constructor.assert_called_once_with(
        batch_delivery.bootstrap_servers,
        logger=None,
        graceful_timeout=0,
        consumer_only=True,
    )
    broker.stop.assert_awaited_once()
    assert broker.options["group_id"] == batch_delivery.group_id
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
    worker = MessageConsumer(
        cast(KafkaBroker, broker), activity_batch_handler(AsyncMock())
    )
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
        raise OSError("failed during drain")

    worker = MessageConsumer(
        cast(KafkaBroker, broker),
        activity_batch_handler(AsyncMock(execute=execute), retry_delays=NO_WAIT),
        shutdown_timeout=0.02,
    )
    stop = asyncio.Event()
    pending = asyncio.create_task(worker.run("test", stop, group_id="group"))
    broker.put(next(iter(broker.queues)), 0)
    await asyncio.wait_for(entered.wait(), 1)
    stop.set()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(OSError, match="failed during drain"):
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
    worker = MessageConsumer(
        cast(KafkaBroker, broker), activity_batch_handler(AsyncMock())
    )
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
        [sys.executable, "-m", "mosemo.entrypoints.consumer"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "Activity Consumer failed\n"
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
            raise OSError("commit failed")

    transaction.__aexit__.side_effect = finish
    session = session_double()
    session.begin.return_value = transaction

    @asynccontextmanager
    async def sessions():
        yield session

    monkeypatch.setattr(
        "mosemo.observation.application.batches.ActivityBatchProcessor.process",
        AsyncMock(),
    )
    broker = Broker()
    broker.commit.side_effect = lambda: acked.set()
    worker = MessageConsumer(
        cast(KafkaBroker, broker),
        activity_batch_handler(
            batch_executor(sessions, clock=FakeClock(utc_now())),
            retry_delays=NO_WAIT,
        ),
    )
    stop = asyncio.Event()
    pending = asyncio.create_task(worker.run("test", stop, group_id="group"))
    broker.put(next(iter(broker.queues)), 0)
    try:
        await asyncio.wait_for(committing.wait(), 1)
        broker.commit.assert_not_called()
        release.set()
        if commit_fails:
            with pytest.raises(OSError, match="commit failed"):
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

    worker = MessageConsumer(
        cast(KafkaBroker, broker), activity_batch_handler(AsyncMock(execute=execute))
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


@pytest.fixture
def consumer_logs(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Deliver the Consumer's records to caplog alone; `mosemo` does not
    propagate once an app configures logging in this process."""
    logger = logging.getLogger(CONSUMER_LOGGER)
    saved = (logger.disabled, logger.propagate, logger.level)
    logger.disabled, logger.propagate = False, False
    logger.setLevel(logging.INFO)
    logger.addHandler(caplog.handler)
    try:
        yield
    finally:
        logger.removeHandler(caplog.handler)
        logger.disabled, logger.propagate = saved[:2]
        logger.setLevel(saved[2])


def formatted(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [json.loads(JsonFormatter().format(r)) for r in caplog.records]


def raw_message(*, valid: bool = True) -> RawMessage:
    msg = message()
    return RawMessage(
        str(msg.account_id).encode(),
        msg.model_dump_json(by_alias=True).encode() if valid else b"private-invalid",
        "activity-batches",
        1,
        7,
    )


POSITION = {"topic": "activity-batches", "partition": 1, "offset": 7}


def failing(*errors: BaseException | None) -> AsyncMock:
    return AsyncMock(execute=AsyncMock(side_effect=list(errors)))


@pytest.mark.asyncio
@pytest.mark.usefixtures("consumer_logs")
async def test_terminal_failure_is_logged_and_acknowledged(caplog):
    executor = failing(TerminalBatchError("device_not_owned"))
    await activity_batch_handler(executor, retry_delays=NO_WAIT)(raw_message())
    assert executor.execute.await_count == 1
    [log] = formatted(caplog)
    assert log["level"] == "WARNING"
    assert log["event"] == "activity batch skipped"
    assert {k: log[k] for k in (*POSITION, "operation", "outcome", "retryable")} == {
        **POSITION,
        "operation": "activity_batch_consume",
        "outcome": "skipped",
        "retryable": False,
    }
    assert log["error_code"] == "device_not_owned"


@pytest.mark.asyncio
@pytest.mark.usefixtures("consumer_logs")
async def test_invalid_message_is_logged_and_acknowledged(caplog):
    executor = failing()
    await activity_batch_handler(executor, retry_delays=NO_WAIT)(
        raw_message(valid=False)
    )
    executor.execute.assert_not_called()
    [log] = formatted(caplog)
    assert log["level"] == "WARNING"
    assert log["outcome"] == "skipped"
    assert log["retryable"] is False
    assert log["error_code"] == "invalid_activity_batch_message"
    assert {k: log[k] for k in POSITION} == POSITION
    assert "private-invalid" not in json.dumps(log)


@pytest.mark.asyncio
@pytest.mark.usefixtures("consumer_logs")
async def test_transient_failure_is_retried_until_it_succeeds(caplog):
    executor = failing(ActivityTimelineBusyError(), ActivityTimelineBusyError(), None)
    await activity_batch_handler(executor, retry_delays=NO_WAIT)(raw_message())
    assert executor.execute.await_count == 3
    logs = formatted(caplog)
    assert [(log["level"], log["outcome"], log["attempt"]) for log in logs] == [
        ("WARNING", "retrying", 1),
        ("WARNING", "retrying", 2),
    ]
    assert all(log["retryable"] is True for log in logs)
    assert all(log["error_code"] == "ActivityTimelineBusyError" for log in logs)
    assert all({k: log[k] for k in POSITION} == POSITION for log in logs)


@pytest.mark.asyncio
@pytest.mark.usefixtures("consumer_logs")
@pytest.mark.parametrize(
    "error",
    [
        ActivityTimelineBusyError(),
        DBAPIError("SELECT 1", None, Exception("private")),
        PoolTimeoutError(),
        OSError("private"),
    ],
    ids=type,
)
async def test_transient_failure_surfaces_after_five_attempts(caplog, error):
    executor = failing(*[error] * 5)
    with pytest.raises(type(error)):
        await activity_batch_handler(executor, retry_delays=NO_WAIT)(raw_message())
    assert executor.execute.await_count == 5
    *retries, failed = formatted(caplog)
    assert [log["attempt"] for log in retries] == [1, 2, 3, 4]
    assert failed["level"] == "ERROR"
    assert failed["event"] == "activity batch failed"
    assert failed["outcome"] == "failed"
    assert failed["retryable"] is False
    assert failed["error_code"] == type(error).__name__
    assert {k: failed[k] for k in POSITION} == POSITION


@pytest.mark.asyncio
async def test_retries_wait_the_given_delays_between_attempts(monkeypatch):
    waits = []

    async def sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(
        "mosemo.entrypoints.consumer.activity_batches.asyncio.sleep", sleep
    )
    executor = failing(*[OSError("down")] * 5)
    with pytest.raises(OSError, match="down"):
        await activity_batch_handler(executor)(raw_message())
    assert waits == [1, 2, 4, 8]


@pytest.mark.asyncio
@pytest.mark.usefixtures("consumer_logs")
async def test_unknown_failure_is_logged_at_error_and_acknowledged(caplog):
    executor = failing(RuntimeError("private-detail"))
    await activity_batch_handler(executor, retry_delays=NO_WAIT)(raw_message())
    assert executor.execute.await_count == 1
    [log] = formatted(caplog)
    assert log["level"] == "ERROR"
    assert log["outcome"] == "skipped"
    assert log["retryable"] is False
    assert log["error_code"] == "RuntimeError"
    assert {k: log[k] for k in POSITION} == POSITION
    assert log["exception"]["type"] == "RuntimeError"
    assert "private-detail" not in json.dumps(log)


@pytest.mark.asyncio
@pytest.mark.usefixtures("consumer_logs")
@pytest.mark.parametrize("error", [RuntimeError, TerminalBatchError, OSError])
async def test_failure_while_cancelling_is_not_skipped(caplog, error):
    async def execute(batch):
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        raise error("cleanup")

    process = activity_batch_handler(AsyncMock(execute=execute), retry_delays=NO_WAIT)

    async def consume():
        await process(raw_message())

    with pytest.raises(error, match="cleanup"):
        await asyncio.create_task(consume())
    assert not caplog.records
