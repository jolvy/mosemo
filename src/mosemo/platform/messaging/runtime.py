"""Kafka client lifecycles for the separate API and Consumer processes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from faststream.kafka import KafkaBroker

from mosemo.platform.messaging.publisher import KafkaPublisher


def consumer_broker(bootstrap_servers: str) -> KafkaBroker:
    # The Consumer only consumes and ACKs; it needs no Producer/Admin client.
    return KafkaBroker(
        bootstrap_servers,
        logger=None,
        graceful_timeout=0,
        consumer_only=True,
    )


@asynccontextmanager
async def kafka_publisher(
    bootstrap_servers: str, *, topic: str
) -> AsyncIterator[KafkaPublisher]:
    broker = KafkaBroker(
        bootstrap_servers,
        logger=None,
        acks="all",
        enable_idempotence=True,
    )
    try:
        await broker.start()
        yield KafkaPublisher(broker, topic=topic)
    finally:
        await broker.stop()
