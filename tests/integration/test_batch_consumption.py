import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import pytest_asyncio
from aiokafka import AIOKafkaProducer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient
from aiokafka.coordinator.protocol import ConsumerProtocolMemberAssignment
from aiokafka.partitioner import DefaultPartitioner
from faststream.kafka import KafkaBroker
from faststream.kafka.message import KafkaAckableMessage
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mosemo.accounts.models import Account, AccountProvider
from mosemo.activities.executor import BatchProcessingExecutor
from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.activities.models import ActivityBatchReceipt, ActivityRecord
from mosemo.activities.schemas import ActivityBatchRequest
from mosemo.devices.models import Device
from mosemo.infra.batch_consumer import ActivityBatchConsumer
from tests.integration.test_activity_batches import batch, record


async def eventually(check, *, timeout=20):
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(0.02)


@pytest_asyncio.fixture
async def consumption_db(integration_database_url):
    engine = create_async_engine(integration_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owners, devices = [], []
    async with sessions() as session:
        for partition in range(2):
            owner = uuid4()
            while (
                DefaultPartitioner()(str(owner).encode(), [0, 1], [0, 1]) != partition
            ):
                owner = uuid4()
            session.add(
                Account(
                    account_id=owner,
                    provider=AccountProvider.KAKAO,
                    provider_subject=str(owner),
                )
            )
            await session.flush()
            device = Device(account_id=owner, idempotency_key=uuid4())
            session.add(device)
            await session.flush()
            owners.append(owner)
            devices.append(device.device_id)
        await session.commit()
    try:
        yield sessions, owners, devices
    finally:
        async with sessions() as session:
            await session.execute(delete(Account).where(Account.account_id.in_(owners)))
            await session.commit()
        await engine.dispose()


class GroupOffsets:
    """Broker admin metadata only; never constructs or polls a Consumer."""

    def __init__(self, admin, group_id):
        self.admin, self.group_id = admin, group_id

    async def committed(self, tp):
        offsets = await self.admin.list_consumer_group_offsets(self.group_id)
        value = offsets.get(tp)
        return value.offset if value is not None and value.offset >= 0 else None

    async def assignments(self):
        response = (await self.admin.describe_consumer_groups([self.group_id]))[0]
        group = response.get_item("groups")[0]
        assert group[0] == 0 and group[1] == self.group_id
        return [
            {
                TopicPartition(topic, p)
                for topic, partitions in ConsumerProtocolMemberAssignment.decode(
                    member[-1]
                ).assignment
                for p in partitions
            }
            for member in group[5]
        ]


@asynccontextmanager
async def running(config, executor):
    broker = KafkaBroker(config.bootstrap_servers, logger=None, graceful_timeout=0)
    worker = ActivityBatchConsumer(broker, executor)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(config.topic, stop, group_id=config.group_id))
    admin = AIOKafkaAdminClient(bootstrap_servers=config.bootstrap_servers)
    try:
        await admin.start()

        async def started():
            if task.done():
                task.result()
            return broker.running

        await eventually(started)
        yield GroupOffsets(admin, config.group_id), stop, task
    finally:
        stop.set()
        await asyncio.wait_for(task, 35)
        await admin.close()


async def publish(config, owners, devices, count):
    producer = AIOKafkaProducer(bootstrap_servers=config.bootstrap_servers, acks="all")
    try:
        await producer.start()
        for partition, (owner, device) in enumerate(zip(owners, devices, strict=True)):
            for sequence in range(count):
                msg = ActivityRegistrationRequested.from_batch(
                    owner,
                    ActivityBatchRequest.model_validate(
                        batch(device, [record(sequence, sequence * 10)])
                    ),
                )
                metadata = await producer.send_and_wait(
                    config.topic,
                    key=str(owner).encode(),
                    value=msg.model_dump_json(by_alias=True).encode(),
                )
                assert metadata.partition == partition
    finally:
        await producer.stop()


async def receipt_count(sessions, owner):
    async with sessions() as session:
        return await session.scalar(
            select(func.count())
            .select_from(ActivityBatchReceipt)
            .where(ActivityBatchReceipt.account_id == owner)
        )


@pytest.mark.asyncio
async def test_real_partitions_parallel_but_next_batch_waits_for_db_and_offset_and_shutdown_only_drains_inflight(
    integration_kafka, consumption_db, monkeypatch
):
    config = integration_kafka.config
    sessions, owners, devices = consumption_db
    entered = asyncio.Queue()
    release = {owner: asyncio.Event() for owner in owners}
    before_commit, allow_commit = asyncio.Event(), asyncio.Event()
    real_executor = BatchProcessingExecutor(sessions)

    class Executor:
        async def execute(self, msg):
            await entered.put((msg.account_id, msg.records[0].sequence))
            await release[msg.account_id].wait()
            await real_executor.execute(msg)

    real_ack = KafkaAckableMessage.ack

    async def ack(message):
        raw = message.raw_message
        if raw.partition == 0 and raw.offset == 0:
            before_commit.set()
            await allow_commit.wait()
        await real_ack(message)

    monkeypatch.setattr(KafkaAckableMessage, "ack", ack)
    await publish(config, owners, devices, 3)
    async with running(config, Executor()) as (consumer, stop, task):
        assignments = await consumer.assignments()
        assert len(assignments) == 2
        assert set.union(*assignments) == {
            TopicPartition(config.topic, p) for p in range(2)
        }
        first = {await asyncio.wait_for(entered.get(), 10) for _ in range(2)}
        assert first == {(owner, 0) for owner in owners}
        release[owners[0]].set()
        await asyncio.wait_for(before_commit.wait(), 10)
        assert await receipt_count(sessions, owners[0]) == 1
        assert await receipt_count(sessions, owners[1]) == 0
        assert entered.empty()  # DB committed, but offset not yet confirmed.
        assert await consumer.committed(TopicPartition(config.topic, 0)) is None
        allow_commit.set()
        assert await asyncio.wait_for(entered.get(), 10) == (owners[0], 1)
        # Stop while partition 1's first batch is still in flight. Partition 0
        # may be in its second/third batch; none may start after stop is set.
        stop.set()
        release[owners[1]].set()
        await asyncio.wait_for(task, 10)
        assert await receipt_count(sessions, owners[1]) == 1
        assert await consumer.committed(TopicPartition(config.topic, 1)) == 1


@pytest.mark.asyncio
async def test_valid_offsets_resume_and_new_group_uses_earliest(
    integration_kafka, integration_kafka_broker, consumption_db
):
    config = integration_kafka.config
    sessions, owners, devices = consumption_db
    seen = []
    real = BatchProcessingExecutor(sessions)

    class Executor:
        async def execute(self, msg):
            seen.append(msg.batch_id)
            await real.execute(msg)

    await publish(config, owners[:1], devices[:1], 1)
    async with running(config, Executor()) as (consumer, _, _):

        async def committed():
            return await consumer.committed(TopicPartition(config.topic, 0)) == 1

        await eventually(committed)
    assert len(seen) == 1
    async with running(config, Executor()) as (consumer, _, _):
        # FastStream resumes this stable group's committed position.
        assert await consumer.committed(TopicPartition(config.topic, 0)) == 1
        await publish(config, owners[:1], devices[:1], 1)

        async def committed_again():
            return await consumer.committed(TopicPartition(config.topic, 0)) == 2

        await eventually(committed_again)
    assert len(seen) == 2 and len(set(seen)) == 2
    fresh = config.model_copy(update={"group_id": config.group_id + ".fresh"})
    try:
        async with running(fresh, Executor()) as (consumer, _, _):

            async def replayed():
                return await consumer.committed(TopicPartition(config.topic, 0)) == 2

            await eventually(replayed)
        assert len(seen) == 4
        assert seen[2:] == seen[:2]
        assert await receipt_count(sessions, owners[0]) == 2
    finally:
        result = integration_kafka_broker.exec(
            [
                "kafka-consumer-groups",
                "--bootstrap-server",
                "localhost:9092",
                "--delete",
                "--group",
                fresh.group_id,
            ]
        )
        assert result.exit_code == 0, result.output.decode()


@pytest.mark.asyncio
async def test_rebalance_between_group_members_cancels_old_session_before_replay(
    integration_kafka, consumption_db
):
    config = integration_kafka.config
    sessions, owners, devices = consumption_db
    entered, cancelled = asyncio.Event(), asyncio.Event()
    real = BatchProcessingExecutor(sessions)
    seen = []

    class HeldExecutor:
        async def execute(self, msg):
            if not entered.is_set():
                try:
                    async with sessions() as session, session.begin():
                        await session.execute(
                            text("SELECT pg_advisory_xact_lock(650065)")
                        )
                        entered.set()
                        await asyncio.Event().wait()
                finally:
                    cancelled.set()
            seen.append(msg.batch_id)
            await real.execute(msg)

    await publish(config, owners, devices, 2)
    async with running(config, HeldExecutor()) as (one, _, _):
        await asyncio.wait_for(entered.wait(), 10)
        async with sessions() as session:
            assert not await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(650065)")
            )
        async with running(config, real) as (_two, _, _):
            await asyncio.wait_for(cancelled.wait(), 15)
            async with sessions() as session:
                assert await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(650065)")
                )
            assignments = await one.assignments()
            assert len(assignments) == 4  # two native FastStream workers per runtime
            assert sum(map(len, assignments)) == 2
            assert set.union(*assignments) == {
                TopicPartition(config.topic, p) for p in range(2)
            }

            async def complete():
                return all(
                    [
                        await one.committed(TopicPartition(config.topic, p)) == 2
                        for p in range(2)
                    ]
                )

            await eventually(complete)
            assert [await receipt_count(sessions, owner) for owner in owners] == [2, 2]
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ActivityRecord)
                        .where(ActivityRecord.device_id.in_(devices))
                    )
                    == 4
                )


@pytest.mark.asyncio
async def test_separate_api_consumer_processes_crash_after_db_commit_replay_without_duplicates(
    integration_kafka, integration_database_url, consumption_db, config, tmp_path
):
    import os
    import socket
    import sys
    from urllib.parse import urlsplit

    import httpx2

    from mosemo.activities.models import ActivityTimelineSegment
    from mosemo.auth.tokens import TokenService

    delivery = integration_kafka.config
    sessions, owners, devices = consumption_db
    parsed = urlsplit(integration_database_url)
    env = os.environ.copy()
    env.update(
        MOSEMO_ENV="test",
        DB_DATABASE=parsed.path.lstrip("/"),
        DB_USER=parsed.username,
        DB_PASSWORD=parsed.password,
        DB_HOST=parsed.hostname,
        DB_PORT=str(parsed.port),
        BATCH_ENABLED="true",
        BATCH_BOOTSTRAP_SERVERS=delivery.bootstrap_servers,
        BATCH_TOPIC=delivery.topic,
        BATCH_GROUP_ID=delivery.group_id,
        AUTH_JWT_SECRET_KEY=config.auth.jwt_secret_key.get_secret_value(),
        AUTH_JWT_ISSUER=config.auth.jwt_issuer,
        AUTH_JWT_AUDIENCE=config.auth.jwt_audience,
        AUTH_MACOS_CALLBACK_URI=config.auth.macos_callback_uri,
        KAKAO_REST_API_KEY="test",
        KAKAO_CLIENT_SECRET="test",
        KAKAO_REDIRECT_URI="http://localhost/test",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    processes = []
    admin = AIOKafkaAdminClient(bootstrap_servers=delivery.bootstrap_servers)
    observer = GroupOffsets(admin, delivery.group_id)
    async with httpx2.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", timeout=3
    ) as client:
        with (tmp_path / "process.log").open("w") as output:
            try:
                await admin.start()
                api = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "mosemo.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                    env=env,
                    stdout=output,
                    stderr=output,
                )
                processes.append(api)

                async def api_ready():
                    assert api.returncode is None
                    try:
                        return (await client.get("/openapi.json")).status_code == 200
                    except httpx2.TransportError:
                        return False

                await eventually(api_ready)
                headers = {
                    "Authorization": "Bearer "
                    + TokenService(config.auth).issue_access_token(owners[0])
                }
                body = batch(devices[0], [record(0, 0)])
                response = await client.post(
                    "/api/v1/activities/batches", json=body, headers=headers
                )
                assert response.status_code == 202
                assert response.json() == {
                    "batchId": body["batchId"],
                    "status": "accepted",
                }
                # No Consumer exists yet: HTTP acceptance depends only on Kafka.
                assert await receipt_count(sessions, owners[0]) == 0
                async with sessions() as session:
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(ActivityRecord)
                            .where(ActivityRecord.device_id == devices[0])
                        )
                        == 0
                    )
                crash = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "tests.batch_consumer_process",
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=output,
                )
                processes.append(crash)
                assert crash.stdout is not None
                line = await asyncio.wait_for(crash.stdout.readline(), 20)
                assert line == b"DB_COMMITTED_BEFORE_OFFSET\n"
                tp = TopicPartition(delivery.topic, 0)
                assert await observer.committed(tp) is None
                assert await receipt_count(sessions, owners[0]) == 1
                async with sessions() as session:
                    receipt_query = select(
                        ActivityBatchReceipt.fingerprint,
                        ActivityBatchReceipt.results,
                        ActivityBatchReceipt.processed_at,
                    ).where(
                        ActivityBatchReceipt.account_id == owners[0],
                        ActivityBatchReceipt.device_id == devices[0],
                        ActivityBatchReceipt.batch_id == body["batchId"],
                    )
                    receipt_before = (await session.execute(receipt_query)).one()
                    before = list(
                        (
                            await session.scalars(
                                select(ActivityTimelineSegment.segment_id).where(
                                    ActivityTimelineSegment.account_id == owners[0]
                                )
                            )
                        ).all()
                    )
                assert before
                crash.kill()
                await asyncio.wait_for(crash.wait(), 5)
                replay = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "mosemo.activities.consumer",
                    env=env,
                    stdout=output,
                    stderr=output,
                )
                processes.append(replay)

                async def replayed():
                    assert replay.returncode is None
                    return await observer.committed(tp) == 1

                await eventually(replayed)
                assert await receipt_count(sessions, owners[0]) == 1
                async with sessions() as session:
                    assert (
                        await session.execute(receipt_query)
                    ).one() == receipt_before
                    after = list(
                        (
                            await session.scalars(
                                select(ActivityTimelineSegment.segment_id).where(
                                    ActivityTimelineSegment.account_id == owners[0]
                                )
                            )
                        ).all()
                    )
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(ActivityRecord)
                            .where(ActivityRecord.device_id == devices[0])
                        )
                        == 1
                    )
                assert after == before
                response = await client.post(
                    "/api/v1/activities/batches",
                    json=batch(devices[0], [record(1, 10)]),
                    headers=headers,
                )
                assert response.status_code == 202

                async def followed():
                    return await observer.committed(tp) == 2

                await eventually(followed)
                assert await receipt_count(sessions, owners[0]) == 2
                replay.terminate()
                assert await asyncio.wait_for(replay.wait(), 35) == 0
            finally:
                for process in reversed(processes):
                    if process.returncode is None:
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), 35)
                        except TimeoutError:
                            process.kill()
                            await process.wait()
                await admin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["account", "schema"])
async def test_registered_faststream_handler_rejects_invalid_message_without_processing_or_ack(
    integration_kafka, consumption_db, invalid
):
    from unittest.mock import AsyncMock

    from mosemo.infra.batch_consumer import InvalidBatchMessage

    config = integration_kafka.config
    _, owners, devices = consumption_db
    broker = KafkaBroker(config.bootstrap_servers, logger=None, graceful_timeout=0)
    executor = AsyncMock()
    worker = ActivityBatchConsumer(broker, executor)
    pending = asyncio.create_task(
        worker.run(config.topic, asyncio.Event(), group_id=config.group_id)
    )
    try:

        async def started():
            if pending.done():
                pending.result()
            return broker.running

        await eventually(started)
        assert len(broker.subscribers) == 1
        assert len(broker.subscribers[0].calls) == 1
        msg = ActivityRegistrationRequested.from_batch(
            owners[0],
            ActivityBatchRequest.model_validate(batch(devices[0], [record(0, 0)])),
        )
        value = (
            msg.model_dump_json(by_alias=True).encode()
            if invalid == "account"
            else b'{"private": "invalid"}'
        )
        await broker.publish(value, topic=config.topic, key=str(uuid4()).encode())
        with pytest.raises(InvalidBatchMessage, match="invalid_activity_batch_message"):
            await asyncio.wait_for(pending, 10)
        executor.execute.assert_not_called()
        assert not broker.running
        offsets = await integration_kafka.admin.list_consumer_group_offsets(
            config.group_id
        )
        assert not offsets
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
