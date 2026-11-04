"""Kafka publication boundary; HTTP acceptance requires the broker ACK."""

import asyncio

from faststream.kafka import KafkaBroker

from mosemo.infra.kafka_compat import KafkaPublicationError


class DeliveryUnavailableError(Exception):
    pass


class KafkaPublisher:
    def __init__(
        self,
        broker: KafkaBroker,
        *,
        topic: str,
        acknowledgment_timeout: float = 5.0,
    ) -> None:
        if acknowledgment_timeout <= 0:
            raise ValueError("acknowledgment timeout must be positive")
        self.broker = broker
        self.topic = topic
        self.acknowledgment_timeout = acknowledgment_timeout

    async def publish(self, *, key: bytes, value: bytes) -> None:
        try:
            async with asyncio.timeout(self.acknowledgment_timeout):
                await self.broker.publish(
                    value,
                    topic=self.topic,
                    key=key,
                    no_confirm=False,
                )
        except (KafkaPublicationError, ConnectionError, TimeoutError) as exc:
            # Timeout does not establish non-publication; retry the same message.
            raise DeliveryUnavailableError from exc
