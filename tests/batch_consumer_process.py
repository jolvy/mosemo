"""Test-only crash barrier at the registered handler's DB/ACK gap."""

import asyncio
from unittest.mock import patch

from faststream.kafka.message import KafkaAckableMessage

from mosemo.activities.consumer import main


async def hold_ack(self):
    print("DB_COMMITTED_BEFORE_OFFSET", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    with patch.object(KafkaAckableMessage, "ack", hold_ack):
        main()
