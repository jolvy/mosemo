"""Kafka backend compatibility: ownership callbacks and publication error type.

FastStream forwards KafkaError unchanged; keep that backend dependency here.
Execution cleanup and error-to-HTTP translation belong to their callers.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from aiokafka import ConsumerRebalanceListener, TopicPartition
from aiokafka.errors import KafkaError

KafkaPublicationError = KafkaError


@dataclass(frozen=True)
class BatchPartition:
    topic: str
    partition: int


class RebalanceTarget(Protocol):
    async def revoke(self, partitions: Iterable[BatchPartition], /) -> None: ...


class BatchRebalanceAdapter(ConsumerRebalanceListener):
    def __init__(self, target: RebalanceTarget) -> None:
        self.target = target

    async def on_partitions_assigned(self, assigned: Iterable[TopicPartition]) -> None:
        # Consumer owns no state for idle assigned partitions.
        pass

    async def on_partitions_revoked(self, revoked: Iterable[TopicPartition]) -> None:
        await self.target.revoke(
            [BatchPartition(tp.topic, tp.partition) for tp in revoked]
        )
