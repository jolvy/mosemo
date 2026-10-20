"""Kafka publication boundary; HTTP acceptance requires the broker ACK."""

import asyncio
from uuid import UUID

from faststream.kafka import KafkaBroker

from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.activities.schemas import ActivityBatchRequest
from mosemo.infra.kafka_compat import KafkaPublicationError


class DeliveryUnavailable(Exception):
    pass


class ActivityBatchPublisher:
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

    async def publish(self, account_id: UUID, batch: ActivityBatchRequest) -> None:
        message = ActivityRegistrationRequested.from_batch(account_id, batch)
        value = message.model_dump_json(by_alias=True).encode("utf-8")
        key = str(account_id).encode("utf-8")
        try:
            async with asyncio.timeout(self.acknowledgment_timeout):
                await self.broker.publish(
                    value,
                    topic=self.topic,
                    key=key,
                    no_confirm=False,
                )
        except (KafkaPublicationError, ConnectionError, TimeoutError) as exc:
            # Timeout does not establish non-publication; retry the same batch.
            raise DeliveryUnavailable from exc
