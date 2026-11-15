"""Docker PostgreSQL and Kafka that the integration and E2E fixtures start."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from testcontainers.community.kafka import KafkaContainer
from testcontainers.community.postgres import PostgresContainer

from tests.support.kafka import KAFKA_IMAGE

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def kafka_broker() -> Iterator[KafkaContainer]:
    """A KRaft broker without topic auto-creation, as Compose runs it."""
    script = ROOT / "scripts/init-kafka-topic.sh"
    with (
        KafkaContainer(KAFKA_IMAGE)
        .with_kraft()
        .with_env("KAFKA_AUTO_CREATE_TOPICS_ENABLE", "false")
        .with_volume_mapping(str(script), "/init-kafka-topic.sh", mode="ro") as broker
    ):
        yield broker


@contextmanager
def migrated_database() -> Iterator[str]:
    """The asyncpg URL of a PostgreSQL migrated to the Alembic head."""
    with PostgresContainer("postgres:18", driver="asyncpg") as postgres:
        database_url = postgres.get_connection_url()

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            database_url,
        )
        command.upgrade(config, "head")

        yield database_url
