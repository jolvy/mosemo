from typing import Protocol
from uuid import UUID


class ActivityDeliveryUnavailableError(Exception):
    """The batch could not be handed to delivery; retry with the same content."""


class ActivityBatchPublisher[B](Protocol):
    """Hands an accepted activity batch to delivery (Kafka, ADR 0006).

    The assembly root implements it with the messaging adapter and raises
    `ActivityDeliveryUnavailableError` when delivery is not confirmed.
    """

    async def publish(self, account_id: UUID, batch: B, /) -> None: ...
