from unittest.mock import AsyncMock

import pytest
from aiokafka.errors import GroupCoordinatorNotAvailableError
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from mosemo.infra.kafka_compat import BatchPartition
from tests.kafka_support import KafkaTestAdmin


@pytest.mark.asyncio
async def test_admin_closes_connection_when_startup_fails(monkeypatch):
    client = AsyncMock()
    client.start.side_effect = ConnectionError("unavailable")
    monkeypatch.setattr("tests.kafka_support.AIOKafkaAdminClient", lambda **kw: client)
    with pytest.raises(ConnectionError):
        async with KafkaTestAdmin("test:9092"):
            pytest.fail("must not enter")
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_offsets_retry_coordinator_readiness_and_preserve_zero(monkeypatch):
    client = AsyncMock()
    client.list_consumer_group_offsets.side_effect = [
        GroupCoordinatorNotAvailableError(),
        {
            TopicPartition("test", 0): OffsetAndMetadata(0, ""),
            TopicPartition("test", 1): OffsetAndMetadata(-1, ""),
        },
    ]
    monkeypatch.setattr("tests.kafka_support.AIOKafkaAdminClient", lambda **kw: client)
    async with KafkaTestAdmin("test:9092") as admin:
        assert await admin.offsets("group") == {BatchPartition("test", 0): 0}
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_topic_preparation_waits_for_metadata_and_every_partition_leader(
    monkeypatch,
):
    client = AsyncMock()
    client.describe_topics.side_effect = [
        [{"error_code": 3, "partitions": []}],  # metadata not propagated yet
        [{"error_code": 5, "partitions": []}],  # leader election in progress
        [{"error_code": 0, "partitions": [{"leader": 0}, {"leader": -1}]}],
        [{"error_code": 0, "partitions": [{"leader": 0}, {"leader": 0}]}],
    ]
    monkeypatch.setattr("tests.kafka_support.AIOKafkaAdminClient", lambda **kw: client)
    async with KafkaTestAdmin("test:9092") as admin:
        assert await admin.prepare_topic("test", 2) == 2
    assert client.describe_topics.await_count == 4
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_topic_preparation_does_not_retry_authorization_failure(monkeypatch):
    from aiokafka.errors import TopicAuthorizationFailedError

    client = AsyncMock()
    client.describe_topics.return_value = [{"error_code": 29, "partitions": []}]
    monkeypatch.setattr("tests.kafka_support.AIOKafkaAdminClient", lambda **kw: client)
    async with KafkaTestAdmin("test:9092") as admin:
        with pytest.raises(TopicAuthorizationFailedError):
            await admin.prepare_topic("test")
    client.describe_topics.assert_awaited_once()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_end_offsets_refreshes_metadata_when_leader_changes(monkeypatch):
    from unittest.mock import Mock

    client = AsyncMock()
    client.describe_topics.side_effect = [
        [{"error_code": 0, "partitions": [{"partition": 0, "leader": 1}]}],
        [{"error_code": 0, "partitions": [{"partition": 0, "leader": 2}]}],
    ]
    stale, ready = Mock(), Mock()
    stale.get_item.return_value = [("test", [(0, 6, -1, -1)])]
    ready.get_item.return_value = [("test", [(0, 0, -1, 0)])]
    client._send_request.side_effect = [stale, ready]
    monkeypatch.setattr("tests.kafka_support.AIOKafkaAdminClient", lambda **kw: client)
    async with KafkaTestAdmin("test:9092") as admin:
        assert await admin.end_offsets("test") == {BatchPartition("test", 0): 0}
    assert [call.args[1] for call in client._send_request.await_args_list] == [1, 2]
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_end_offsets_does_not_retry_authorization_failure(monkeypatch):
    from unittest.mock import Mock

    from aiokafka.errors import TopicAuthorizationFailedError

    client = AsyncMock()
    client.describe_topics.return_value = [
        {"error_code": 0, "partitions": [{"partition": 0, "leader": 1}]}
    ]
    denied = Mock()
    denied.get_item.return_value = [("test", [(0, 29, -1, -1)])]
    client._send_request.return_value = denied
    monkeypatch.setattr("tests.kafka_support.AIOKafkaAdminClient", lambda **kw: client)
    async with KafkaTestAdmin("test:9092") as admin:
        with pytest.raises(TopicAuthorizationFailedError):
            await admin.end_offsets("test")
    client._send_request.assert_awaited_once()
    client.close.assert_awaited_once()
