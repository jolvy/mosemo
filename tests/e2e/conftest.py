from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from mosemo.entrypoints.api.config import ApiConfig
from mosemo.platform.messaging.config import BatchDeliveryConfig
from tests.support.containers import kafka_broker, migrated_database
from tests.support.e2e import E2eSystem, process_environment, running_system
from tests.support.kafka import KafkaTestAdmin


@pytest.fixture(scope="session")
def e2e_database_url() -> Iterator[str]:
    with migrated_database() as database_url:
        yield database_url


@pytest.fixture(scope="session")
def e2e_kafka_broker():
    with kafka_broker() as broker:
        yield broker


@pytest_asyncio.fixture
async def e2e_kafka(e2e_kafka_broker) -> AsyncIterator[BatchDeliveryConfig]:
    """The topic the API publishes to and the Consumer reads, created up front
    as `kafka-init` does (the broker does not auto-create topics)."""
    suffix = uuid4().hex
    config = BatchDeliveryConfig(
        bootstrap_servers=e2e_kafka_broker.get_bootstrap_server(),
        topic=f"e2e.activities.{suffix}",
        group_id=f"e2e.activity-batches.{suffix}",
    )
    async with KafkaTestAdmin(config.bootstrap_servers) as admin:
        try:
            assert await admin.prepare_topic(config.topic) == 2
            yield config
        finally:
            try:
                await admin.delete_group(config.group_id)
            finally:
                await admin.delete_topic(config.topic)


@pytest.fixture
def e2e_system(
    e2e_database_url: str,
    e2e_kafka: BatchDeliveryConfig,
    api_config: ApiConfig,
    tmp_path: Path,
) -> Iterator[E2eSystem]:
    environment = process_environment(e2e_database_url, api_config, e2e_kafka)
    with running_system(environment, tmp_path) as system:
        yield system
