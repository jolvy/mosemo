"""Real Kafka resources shared by broker and future batch integration tests."""

import asyncio
from dataclasses import dataclass

from aiokafka.admin import AIOKafkaAdminClient

from mosemo.config import BatchDeliveryConfig

KAFKA_IMAGE = "confluentinc/cp-kafka:7.8.0"


@dataclass(frozen=True)
class KafkaResources:
    config: BatchDeliveryConfig
    admin: AIOKafkaAdminClient


async def wait_for_partitions(
    admin: AIOKafkaAdminClient, topic: str, count: int
) -> None:
    """Require all partition leaders to be ready, with a finite deadline."""
    async with asyncio.timeout(30):
        while True:
            descriptions = await admin.describe_topics([topic])
            partitions = descriptions[0]["partitions"]
            if len(partitions) == count and all(p["leader"] >= 0 for p in partitions):
                return
            await asyncio.sleep(0.1)
