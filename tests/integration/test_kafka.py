import asyncio
import json
from contextlib import AsyncExitStack
from uuid import uuid4

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.structs import TopicPartition

from tests.kafka_support import KafkaResources, wait_for_partitions


@pytest.mark.asyncio
async def test_real_broker_publishes_and_consumes_both_partitions(
    integration_kafka: KafkaResources,
):
    config = integration_kafka.config
    account_id = str(uuid4())
    payload = json.dumps(
        {
            "accountId": account_id,
            "batchId": str(uuid4()),
            "deviceId": str(uuid4()),
            "records": [],
        }
    ).encode()
    async with AsyncExitStack() as stack:
        producer = AIOKafkaProducer(
            bootstrap_servers=config.bootstrap_servers,
            acks="all",
            enable_idempotence=True,
        )
        stack.push_async_callback(producer.stop)
        await producer.start()
        consumer = AIOKafkaConsumer(
            config.topic,
            bootstrap_servers=config.bootstrap_servers,
            group_id=config.group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        stack.push_async_callback(consumer.stop)
        await consumer.start()
        async with asyncio.timeout(30):
            for partition in (0, 1):
                metadata = await producer.send_and_wait(
                    config.topic, payload, key=account_id.encode(), partition=partition
                )
                assert metadata.partition == partition
            received = [await consumer.getone(), await consumer.getone()]
            assert {record.partition for record in received} == {0, 1}
            assert all(record.key == account_id.encode() for record in received)
            assert all(record.value == payload for record in received)
            await consumer.commit()
            for record in received:
                position = TopicPartition(config.topic, record.partition)
                assert await consumer.committed(position) == record.offset + 1


@pytest.mark.asyncio
async def test_topic_reinitialization_does_not_resize_existing_partitions(
    integration_kafka: KafkaResources,
    integration_kafka_broker,
):
    config = integration_kafka.config
    result = integration_kafka_broker.exec(
        [
            "env",
            f"BATCH_TOPIC={config.topic}",
            "BATCH_TOPIC_PARTITIONS=3",
            "sh",
            "/init-kafka-topic.sh",
            "localhost:9092",
        ]
    )
    assert result.exit_code == 0, result.output.decode()
    await wait_for_partitions(integration_kafka.admin, config.topic, 2)
