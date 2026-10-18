"""The registered FastStream subscriber is the activity consumption boundary."""

from collections.abc import Awaitable, Callable

from aiokafka import ConsumerRebalanceListener
from faststream import AckPolicy
from faststream.kafka import KafkaBroker
from faststream.kafka.annotations import KafkaMessage


def register_activity_handler(
    broker: KafkaBroker,
    handle: Callable[..., Awaitable[None]],
    *,
    topic: str,
    group_id: str,
    listener: ConsumerRebalanceListener,
):
    subscriber = broker.subscriber(
        topic,
        group_id=group_id,
        auto_offset_reset="earliest",
        ack_policy=AckPolicy.MANUAL,
        max_workers=2,
        listener=listener,
        decoder=lambda message: message.body,
        no_reply=True,
    )

    @subscriber
    async def register_requested(message: KafkaMessage) -> None:
        await handle(message)

    return subscriber
