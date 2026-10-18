import asyncio
import json
from unittest.mock import AsyncMock

import httpx2
import pytest
from aiokafka import AIOKafkaConsumer
from asgi_lifespan import LifespanManager

from mosemo.activities.batch_processing import ActivityBatchProcessor
from mosemo.activities.messages import ActivityRegistrationRequested
from tests.component.test_activities_api import activity_request
from tests.component.test_batch_runtime import make_app


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum_body", [False, True])
async def test_http_waits_for_real_publication_ack_without_running_db_processing(
    integration_kafka, config, monkeypatch, maximum_body
):
    config.batch_delivery = integration_kafka.config
    app, _, owner, headers = make_app(config)
    process = AsyncMock(side_effect=AssertionError("API must not run consumer"))
    monkeypatch.setattr(ActivityBatchProcessor, "process", process)
    payload = activity_request()
    payload["records"][0]["observedAt"] = "2026-09-14T00:00:00.123456Z"
    if maximum_body:
        payload["records"][0]["context"] = {
            "kind": "detailed",
            "app": {
                "bundleId": {"status": "absent"},
                "name": {"status": "captured", "value": "가" * 170_000},
            },
            "window": {"status": "absent"},
            "web": {"kind": "not_applicable"},
        }
    body = json.dumps(payload, ensure_ascii=False).encode()
    if maximum_body:
        body += b" " * (512 * 1024 - len(body))
        assert len(body) == 512 * 1024
    headers |= {"Content-Type": "application/json"}
    entered, release, acknowledged = asyncio.Event(), asyncio.Event(), asyncio.Event()
    consumer = AIOKafkaConsumer(
        config.batch_delivery.topic,
        bootstrap_servers=config.batch_delivery.bootstrap_servers,
        group_id=config.batch_delivery.group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    try:
        await consumer.start()
        async with (
            LifespanManager(app) as manager,
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=manager.app), base_url="http://test"
            ) as client,
        ):
            producer = app.state.batch_publisher.producer
            real_send = producer.send_and_wait

            async def held_send(*args, **kwargs):
                entered.set()
                await release.wait()
                metadata = await real_send(*args, **kwargs)
                acknowledged.set()
                return metadata

            monkeypatch.setattr(producer, "send_and_wait", held_send)
            pending = asyncio.create_task(
                client.post("/api/v1/activities/batches", headers=headers, content=body)
            )
            try:
                await asyncio.wait_for(entered.wait(), 3)
                assert not pending.done()
                assert not acknowledged.is_set()
            finally:
                release.set()
            response = await asyncio.wait_for(pending, 10)
            assert acknowledged.is_set()
            assert response.status_code == 202
            assert response.json() == {
                "batchId": payload["batchId"],
                "status": "accepted",
            }
            record = await asyncio.wait_for(consumer.getone(), 10)
            assert record.value is not None
            message = ActivityRegistrationRequested.model_validate_json(record.value)
            assert message.account_id == owner
            assert record.key == str(owner).encode()
            assert not any(
                key.lower() in {"accountid", "x-account-id", "account-id"}
                for key, _ in record.headers
            )
            assert json.loads(record.value) == payload | {
                "accountId": str(owner),
                "records": [
                    r.model_dump(mode="json", by_alias=True) for r in message.records
                ],
            }
            assert message.records[0].observed_at.microsecond == 123456
            process.assert_not_called()
        assert app.state.batch_publisher is None
    finally:
        await consumer.stop()
