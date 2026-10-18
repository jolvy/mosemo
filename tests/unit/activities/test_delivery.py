import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiokafka.errors import KafkaConnectionError, MessageSizeTooLargeError
from pydantic import ValidationError

from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.activities.schemas import ActivityBatchRequest
from mosemo.infra.batch_delivery import ActivityBatchPublisher, DeliveryUnavailable
from tests.component.test_activities_api import activity_request


def test_message_roundtrip_preserves_record_order_precision_and_validation():
    payload = activity_request()
    payload["records"][0]["observedAt"] = "2026-09-14T00:00:00.123456Z"
    payload["records"].append(
        payload["records"][0] | {"eventId": str(uuid4()), "sequence": 4}
    )
    batch = ActivityBatchRequest.model_validate(payload)
    owner = uuid4()
    message = ActivityRegistrationRequested.from_batch(owner, batch)
    encoded = message.model_dump_json(by_alias=True)
    assert set(json.loads(encoded)) == {"accountId", "batchId", "deviceId", "records"}
    assert json.loads(encoded)["records"][0]["observedAt"].endswith(".123456Z")
    assert ActivityRegistrationRequested.model_validate_json(encoded) == message
    assert [r.sequence for r in message.records] == [3, 4]
    invalid = json.loads(encoded)
    invalid["records"][0]["timezoneId"] = "Europe/Paris"
    with pytest.raises(ValidationError):
        ActivityRegistrationRequested.model_validate(invalid)


@pytest.mark.asyncio
async def test_publisher_waits_for_ack_with_account_key_and_no_headers():
    entered, release = asyncio.Event(), asyncio.Event()
    producer = AsyncMock()

    async def held(*args, **kwargs):
        entered.set()
        await release.wait()

    producer.send_and_wait.side_effect = held
    publisher = ActivityBatchPublisher(producer, topic="test.activities")
    assert publisher.acknowledgment_timeout == 5.0
    owner = uuid4()
    batch = ActivityBatchRequest.model_validate(activity_request())
    pending = asyncio.create_task(publisher.publish(owner, batch))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not pending.done()
    finally:
        release.set()
        await asyncio.wait_for(pending, 1)
    call = producer.send_and_wait.call_args
    assert call.args[0] == "test.activities"
    assert call.kwargs == {"key": str(owner).encode()}
    assert (
        ActivityRegistrationRequested.model_validate_json(call.args[1]).account_id
        == owner
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [KafkaConnectionError(), MessageSizeTooLargeError(), ConnectionError()]
)
async def test_publisher_translates_transport_failures(failure):
    producer = AsyncMock()
    producer.send_and_wait.side_effect = failure
    with pytest.raises(DeliveryUnavailable):
        await ActivityBatchPublisher(producer, topic="test").publish(
            uuid4(), ActivityBatchRequest.model_validate(activity_request())
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_ack_deadline_and_external_cancellation_clean_up_waiter(cancel):
    entered, cleaned = asyncio.Event(), asyncio.Event()
    producer = AsyncMock()

    async def held(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    producer.send_and_wait.side_effect = held
    publisher = ActivityBatchPublisher(
        producer, topic="test", acknowledgment_timeout=0.02 if not cancel else 5
    )
    pending = asyncio.create_task(
        publisher.publish(
            uuid4(), ActivityBatchRequest.model_validate(activity_request())
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    if cancel:
        pending.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else DeliveryUnavailable):
        await asyncio.wait_for(pending, 1)
    assert cleaned.is_set()
