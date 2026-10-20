import asyncio
import json
from uuid import uuid4

import pytest
from faststream.kafka import KafkaBroker

from mosemo.infra.kafka_compat import BatchPartition
from tests.kafka_support import KafkaResources, capture_records


@pytest.mark.asyncio
async def test_real_broker_publishes_and_consumes_both_partitions(
    integration_kafka: KafkaResources,
):
    config = integration_kafka.config
    account_id = str(uuid4())
    payload = json.dumps({"accountId": account_id, "records": []}).encode()
    broker = KafkaBroker(
        config.bootstrap_servers, acks="all", enable_idempotence=True, logger=None
    )
    try:
        await broker.start()
        assert await integration_kafka.admin.offsets(config.group_id) == {}
        assert set(
            (await integration_kafka.admin.end_offsets(config.topic)).values()
        ) == {0}
        async with capture_records(config) as records, asyncio.timeout(30):
            for partition in (0, 1):
                metadata = await broker.publish(
                    payload,
                    topic=config.topic,
                    key=account_id.encode(),
                    partition=partition,
                    no_confirm=False,
                )
                assert metadata.partition == partition
            received = [await records.get(), await records.get()]
            assert {record.partition for record in received} == {0, 1}
            assert all(record.key == account_id.encode() for record in received)
            assert all(record.value == payload for record in received)
            assert await integration_kafka.admin.offsets(config.group_id) == {
                BatchPartition(config.topic, p): 1 for p in (0, 1)
            }
            assignments = await integration_kafka.admin.assignments(config.group_id)
            assert set.union(*assignments) == {
                BatchPartition(config.topic, p) for p in (0, 1)
            }
        assert set(
            (await integration_kafka.admin.end_offsets(config.topic)).values()
        ) == {1}
        await integration_kafka.admin.delete_group(config.group_id)
        assert config.group_id not in await integration_kafka.admin.groups()
        await integration_kafka.admin.delete_topic(config.topic)
        assert config.topic not in await integration_kafka.admin.topics()
    finally:
        await broker.stop()


@pytest.mark.asyncio
async def test_admin_reinitialization_and_local_cli_preserve_existing_partitions(
    integration_kafka: KafkaResources,
    integration_kafka_broker,
):
    config = integration_kafka.config
    assert await integration_kafka.admin.prepare_topic(config.topic, 3) == 2
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
    assert await integration_kafka.admin.prepare_topic(config.topic) == 2
