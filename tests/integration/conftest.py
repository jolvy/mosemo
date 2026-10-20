from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from testcontainers.community.kafka import KafkaContainer
from testcontainers.community.postgres import PostgresContainer

from mosemo.config import BatchDeliveryConfig
from tests.kafka_support import KAFKA_IMAGE, KafkaResources, KafkaTestAdmin


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
    async with KafkaTestAdmin(config.bootstrap_servers) as admin:
        try:
            assert await admin.prepare_topic(config.topic) == 2
            yield KafkaResources(config=config, admin=admin)
        finally:
            try:
                await admin.delete_group(config.group_id)
            finally:
                await admin.delete_topic(config.topic)


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
