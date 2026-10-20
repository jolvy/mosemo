import asyncio
from unittest.mock import AsyncMock

import pytest
from aiokafka import TopicPartition

from mosemo.infra.kafka_compat import BatchPartition, BatchRebalanceAdapter


@pytest.mark.asyncio
async def test_adapter_converts_only_revoked_partitions_and_waits_for_cleanup():
    entered, release = asyncio.Event(), asyncio.Event()
    target = AsyncMock()

    async def revoke(partitions):
        assert partitions == [BatchPartition("activities", 1)]
        entered.set()
        await release.wait()

    target.revoke.side_effect = revoke
    adapter = BatchRebalanceAdapter(target)
    await adapter.on_partitions_assigned([TopicPartition("activities", 0)])
    target.revoke.assert_not_called()
    pending = asyncio.create_task(
        adapter.on_partitions_revoked([TopicPartition("activities", 1)])
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not pending.done()
    finally:
        release.set()
        await asyncio.wait_for(pending, 1)
    target.revoke.assert_awaited_once()
