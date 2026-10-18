"""Kafka publication boundary; HTTP acceptance requires the broker ACK."""

import asyncio
from uuid import UUID

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.activities.schemas import ActivityBatchRequest


class DeliveryUnavailable(Exception):
    pass


class ActivityBatchPublisher:
    def __init__(
        self,
        producer: AIOKafkaProducer,
        *,
        topic: str,
        acknowledgment_timeout: float = 5.0,
    ) -> None:
        if acknowledgment_timeout <= 0:
            raise ValueError("acknowledgment timeout must be positive")
        self.producer = producer
        self.topic = topic
        self.acknowledgment_timeout = acknowledgment_timeout

    async def publish(self, account_id: UUID, batch: ActivityBatchRequest) -> None:
        message = ActivityRegistrationRequested.from_batch(account_id, batch)
        try:
            async with asyncio.timeout(self.acknowledgment_timeout):
                await self.producer.send_and_wait(
                    self.topic,
                    message.model_dump_json(by_alias=True).encode("utf-8"),
                    key=str(account_id).encode("utf-8"),
                )
        except (KafkaError, ConnectionError, TimeoutError) as exc:
            # Timeout does not establish non-publication; retry the same batch.
            raise DeliveryUnavailable from exc
