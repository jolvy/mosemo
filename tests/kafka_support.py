"""Isolated Kafka administration and FastStream message capture for verification.

aiokafka 0.14 has no public delete-group or end-offset Admin methods. The two
protocol requests below stay here, together with response/error decoding. No
application producer/consumer or FastStream private state is accessed.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.coordinator.protocol import ConsumerProtocolMemberAssignment
from aiokafka.errors import (
    GroupCoordinatorNotAvailableError,
    GroupLoadInProgressError,
    LeaderNotAvailableError,
    NotCoordinatorForGroupError,
    NotLeaderForPartitionError,
    TopicAlreadyExistsError,
    UnknownTopicOrPartitionError,
    for_code,
)
from aiokafka.partitioner import DefaultPartitioner
from aiokafka.protocol.admin import DeleteGroupsRequest
from aiokafka.protocol.offset import OffsetRequest
from faststream import AckPolicy
from faststream.kafka import KafkaBroker
from faststream.kafka.annotations import KafkaMessage

from mosemo.config import BatchDeliveryConfig
from mosemo.infra.kafka_compat import BatchPartition

KAFKA_IMAGE = "confluentinc/cp-kafka:7.8.0"


class KafkaTestAdmin:
    def __init__(self, bootstrap_servers: str):
        self._admin = AIOKafkaAdminClient(
            bootstrap_servers=bootstrap_servers, request_timeout_ms=10000
        )

    async def __aenter__(self):
        try:
            await self._admin.start()
        except BaseException:
            await self._admin.close()
            raise
        return self

    async def __aexit__(self, *exc):
        await self._admin.close()

    async def prepare_topic(self, topic: str, partitions: int = 2) -> int:
        try:
            await self._admin.create_topics([NewTopic(topic, partitions, 1)])
        except TopicAlreadyExistsError:
            pass
        # Existing topics retain their partition count, as the local CLI does.
        return len(await self._ready_partitions(topic))

    async def _ready_partitions(self, topic: str) -> list[dict]:
        async with asyncio.timeout(30):
            while True:
                description = (await self._admin.describe_topics([topic]))[0]
                if description["error_code"]:
                    error = for_code(description["error_code"])
                    if error not in (
                        UnknownTopicOrPartitionError,
                        LeaderNotAvailableError,
                    ):
                        raise error("describe test topic")
                    # Controller creation can precede broker metadata/leader readiness.
                    await asyncio.sleep(0.1)
                    continue
                parts = description["partitions"]
                if parts and all(p["leader"] >= 0 for p in parts):
                    return parts
                await asyncio.sleep(0.1)

    async def topics(self) -> set[str]:
        return set(await self._admin.list_topics())

    async def groups(self) -> set[str]:
        return {group for group, _ in await self._admin.list_consumer_groups()}

    async def delete_group(self, group_id: str) -> None:
        if group_id not in await self.groups():
            return
        node = await self._admin.find_coordinator(group_id)
        response = await self._admin._send_request(
            DeleteGroupsRequest([group_id]), node
        )
        for _, code in response.get_item("results"):
            if code:
                raise for_code(code)("delete test group")
        async with asyncio.timeout(30):
            while group_id in await self.groups():
                await asyncio.sleep(0.1)

    async def delete_topic(self, topic: str) -> None:
        if topic not in await self.topics():
            return
        response = await self._admin.delete_topics([topic])
        for _, code in response.get_item("topic_error_codes"):
            if code:
                raise for_code(code)("delete test topic")
        async with asyncio.timeout(30):
            while topic in await self.topics():
                await asyncio.sleep(0.1)

    async def offsets(self, group_id: str) -> dict[BatchPartition, int]:
        async with asyncio.timeout(30):
            while True:
                try:
                    raw = await self._admin.list_consumer_group_offsets(group_id)
                    break
                except (
                    GroupCoordinatorNotAvailableError,
                    GroupLoadInProgressError,
                    NotCoordinatorForGroupError,
                ):
                    # The offsets topic/coordinator may still be initializing.
                    await asyncio.sleep(0.1)
        return {
            BatchPartition(tp.topic, tp.partition): value.offset
            for tp, value in raw.items()
            if value.offset >= 0
        }

    async def assignments(self, group_id: str) -> list[set[BatchPartition]]:
        response = (await self._admin.describe_consumer_groups([group_id]))[0]
        group = response.get_item("groups")[0]
        if group[0]:
            raise for_code(group[0])("describe test group")
        return [
            {
                BatchPartition(topic, p)
                for topic, partitions in ConsumerProtocolMemberAssignment.decode(
                    member[-1]
                ).assignment
                for p in partitions
            }
            for member in group[5]
        ]

    async def end_offsets(self, topic: str) -> dict[BatchPartition, int]:
        async with asyncio.timeout(30):
            while True:
                partitions = await self._ready_partitions(topic)
                try:
                    result = {}
                    for part in partitions:
                        response = await self._admin._send_request(
                            OffsetRequest(-1, 0, [(topic, [(part["partition"], -1)])]),
                            part["leader"],
                        )
                        for name, offsets in response.get_item("topics"):
                            for partition, code, _timestamp, offset in offsets:
                                if code:
                                    raise for_code(code)("test end offset")
                                result[BatchPartition(name, partition)] = offset
                    return result
                except (
                    UnknownTopicOrPartitionError,
                    LeaderNotAvailableError,
                    NotLeaderForPartitionError,
                ):
                    # A metadata snapshot can precede leader readiness/change.
                    # Refresh the leaders, not just the request to the stale node.
                    await asyncio.sleep(0.1)

    @staticmethod
    def partition_for(key: bytes, count: int = 2) -> int:
        partitions = list(range(count))
        return DefaultPartitioner()(key, partitions, partitions)


class GroupOffsets:
    def __init__(self, admin: KafkaTestAdmin, group_id: str):
        self.admin, self.group_id = admin, group_id

    async def committed(self, partition: BatchPartition) -> int | None:
        return (await self.admin.offsets(self.group_id)).get(partition)

    async def assignments(self) -> list[set[BatchPartition]]:
        return await self.admin.assignments(self.group_id)


@dataclass(frozen=True)
class KafkaResources:
    config: BatchDeliveryConfig
    admin: KafkaTestAdmin


@dataclass(frozen=True)
class PublishedRecord:
    key: bytes | None
    value: bytes | None
    headers: tuple[tuple[str, bytes], ...]
    partition: int
    offset: int


@asynccontextmanager
async def capture_records(config: BatchDeliveryConfig) -> AsyncIterator[asyncio.Queue]:
    queue = asyncio.Queue()
    broker = KafkaBroker(config.bootstrap_servers, logger=None)

    @broker.subscriber(
        config.topic,
        group_id=config.group_id,
        auto_offset_reset="earliest",
        ack_policy=AckPolicy.MANUAL,
        decoder=lambda message: message.body,
    )
    async def capture(message: KafkaMessage):
        raw = message.raw_message
        assert not isinstance(raw, tuple)
        record = PublishedRecord(
            raw.key, raw.value, tuple(raw.headers), raw.partition, raw.offset
        )
        await message.ack()
        await queue.put(record)

    try:
        await broker.start()
        yield queue
    finally:
        await broker.stop()
