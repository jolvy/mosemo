import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.infra.batch_delivery import (
    BatchDelivery,
    DeliveryUnavailable,
    RetainedMessageState,
)

TEST_TOPIC = "test.activities.registration-requested"


def message(device=None):
    return ActivityRegistrationRequested(
        uuid4(), device or uuid4(), uuid4(), b'{"original":true}'
    )


@pytest.mark.asyncio
async def test_active_failure_and_blocked_source_share_capacity_and_recover_in_order():
    broker = AsyncMock()
    broker.publish.side_effect = RuntimeError("sensitive text never retained")
    delivery = BatchDelivery(broker, topic=TEST_TOPIC, capacity=3)
    first = message()
    second = ActivityRegistrationRequested(
        first.account_id, first.device_id, uuid4(), b'"second"'
    )
    third = ActivityRegistrationRequested(
        first.account_id, first.device_id, uuid4(), b'"third"'
    )
    async with delivery.lifespan():
        key = delivery.admit(first)
        delivery.admit(second)
        delivery.admit(third)
        await delivery.wait_idle()
        assert [v.state for v in delivery.inspect().values()] == [
            RetainedMessageState.FAILED,
            RetainedMessageState.BLOCKED,
            RetainedMessageState.BLOCKED,
        ]
        assert next(iter(delivery.inspect().values())).error_code == "RuntimeError"
        with pytest.raises(DeliveryUnavailable):
            delivery.admit(message())
        broker.publish.side_effect = None
        delivery.reprocess(key)
        await delivery.wait_idle()
        assert delivery.inspect() == {}
        assert [
            c.args[0]["source"].encode() for c in broker.publish.await_args_list
        ] == [
            first.body,
            first.body,
            second.body,
            third.body,
        ]
        assert all(c.args[1] == TEST_TOPIC for c in broker.publish.await_args_list)
    assert delivery.worker is not None and delivery.worker.done()


@pytest.mark.asyncio
async def test_transient_retry_uses_five_attempts_full_jitter_and_other_device_continues():
    broker = AsyncMock()
    broker.publish.side_effect = [ConnectionError()] * 5 + [None]
    sleep = AsyncMock()
    delivery = BatchDelivery(broker, topic=TEST_TOPIC, sleep=sleep, jitter=lambda: 0.5)
    async with delivery.lifespan():
        delivery.admit(message())
        delivery.admit(message())
        await delivery.wait_idle()
        assert [c.args[0] for c in sleep.await_args_list] == [0.5, 1, 2, 4]
        assert broker.publish.await_count == 6
        assert len(delivery.inspect()) == 1
        assert next(iter(delivery.inspect().values())).attempts == 5


@pytest.mark.asyncio
async def test_cancellation_retains_active_source_and_stops_admission():
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def publish(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    broker = AsyncMock()
    broker.publish.side_effect = publish
    delivery = BatchDelivery(broker, topic=TEST_TOPIC, capacity=1)
    async with delivery.lifespan():
        key = delivery.admit(message())
        assert delivery.inspect()[key].state is RetainedMessageState.QUEUED
        await entered.wait()
        assert delivery.inspect()[key].state is RetainedMessageState.ACTIVE
        with pytest.raises(DeliveryUnavailable):
            delivery.admit(message())
    assert closed.is_set()
    assert delivery.inspect()[key].state is RetainedMessageState.FAILED
    assert next(iter(delivery.inspect().values())).error_code == "shutdown_cancelled"
    with pytest.raises(DeliveryUnavailable):
        delivery.admit(message())


@pytest.mark.asyncio
async def test_fatal_worker_is_observed_and_refuses_admission_without_leaked_task():
    broker = AsyncMock()
    broker.publish.side_effect = ConnectionError()

    async def broken_sleep(delay):
        raise RuntimeError("controlled fatal worker")

    delivery = BatchDelivery(broker, topic=TEST_TOPIC, sleep=broken_sleep)
    async with delivery.lifespan():
        delivery.admit(message())
        await asyncio.wait_for(delivery.wait_idle(), 3)
        await asyncio.sleep(0)  # Run the completion observer.
        assert delivery.fatal_error_code == "RuntimeError"
        with pytest.raises(DeliveryUnavailable):
            delivery.admit(message())
    assert delivery.worker is not None and delivery.worker.done()
    assert next(iter(delivery.inspect().values())).state is RetainedMessageState.FAILED
