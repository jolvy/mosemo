import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from aiokafka.admin import AIOKafkaAdminClient
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from testcontainers.community.kafka import KafkaContainer
from testcontainers.community.postgres import PostgresContainer

from mosemo.config import BatchDeliveryConfig
from tests.kafka_support import KAFKA_IMAGE, KafkaResources, wait_for_partitions


@pytest.fixture(scope="session")
def integration_kafka_broker():
    script = Path(__file__).resolve().parents[2] / "scripts/init-kafka-topic.sh"
    with (
        KafkaContainer(KAFKA_IMAGE)
        .with_kraft()
        .with_env("KAFKA_AUTO_CREATE_TOPICS_ENABLE", "false")
        .with_volume_mapping(str(script), "/init-kafka-topic.sh", mode="ro") as broker
    ):
        yield broker


@pytest_asyncio.fixture
async def integration_kafka(integration_kafka_broker) -> AsyncIterator[KafkaResources]:
    broker = integration_kafka_broker
    suffix = uuid4().hex
    config = BatchDeliveryConfig(
        bootstrap_servers=broker.get_bootstrap_server(),
        topic=f"test.activities.{suffix}",
        group_id=f"test.activity-batches.{suffix}",
    )
    admin = AIOKafkaAdminClient(
        bootstrap_servers=config.bootstrap_servers, request_timeout_ms=10000
    )
    try:
        await admin.start()
        result = broker.exec(
            [
                "env",
                f"BATCH_TOPIC={config.topic}",
                "sh",
                "/init-kafka-topic.sh",
                "localhost:9092",
            ]
        )
        assert result.exit_code == 0, result.output.decode()
        await wait_for_partitions(admin, config.topic, 2)
        yield KafkaResources(config=config, admin=admin)
    finally:
        try:
            groups = await admin.list_consumer_groups()
            if any(group_id == config.group_id for group_id, _ in groups):
                result = broker.exec(
                    [
                        "kafka-consumer-groups",
                        "--bootstrap-server",
                        "localhost:9092",
                        "--delete",
                        "--group",
                        config.group_id,
                    ]
                )
                assert result.exit_code == 0, result.output.decode()
                assert config.group_id not in dict(await admin.list_consumer_groups())
            if config.topic in await admin.list_topics():
                await admin.delete_topics([config.topic])
                async with asyncio.timeout(30):
                    while config.topic in await admin.list_topics():
                        await asyncio.sleep(0.1)
        finally:
            await admin.close()


@pytest.fixture(scope="session")
def integration_database_url():
    with PostgresContainer("postgres:18", driver="asyncpg") as postgres:
        database_url = postgres.get_connection_url()

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            database_url,
        )
        command.upgrade(config, "head")

        yield database_url


@pytest_asyncio.fixture
async def integration_session(
    integration_database_url: str,
) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(integration_database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                yield session
            finally:
                await session.close()
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()
