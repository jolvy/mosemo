"""Real batch HTTP submission plus explicit eventual consumer/DB observation."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID, uuid4

import httpx2
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.activities.handler import get_batch_session
from mosemo.activities.models import ActivityBatchReceipt
from mosemo.config import BatchDeliveryConfig
from mosemo.infra.batch_runtime import compose_development_app


class BatchClient(httpx2.AsyncClient):
    api: FastAPI
    session_factory: async_sessionmaker[AsyncSession]


@asynccontextmanager
async def batch_client(
    api: FastAPI, factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[BatchClient]:
    async def message_session():
        async with factory() as session:
            yield session

    api.dependency_overrides[get_batch_session] = message_session
    app = compose_development_app(api, BatchDeliveryConfig())
    async with (
        LifespanManager(app) as manager,
        BatchClient(
            transport=httpx2.ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        client.api = app
        client.session_factory = factory
        yield client


async def receipt_for(
    client: httpx2.AsyncClient, response: httpx2.Response
) -> ActivityBatchReceipt:
    test_client = cast(BatchClient, client)
    await asyncio.wait_for(test_client.api.state.batch_runtime.delivery.wait_idle(), 10)
    async with test_client.session_factory() as session:
        receipt = await session.scalar(
            select(ActivityBatchReceipt).where(
                ActivityBatchReceipt.batch_id == UUID(response.json()["batchId"])
            )
        )
    assert receipt is not None
    return receipt


async def post_activity(
    client: httpx2.AsyncClient,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
    batch_id: UUID | None = None,
    expected_result: str | None = None,
    expected_failure: str | None = None,
) -> httpx2.Response:
    batch = {
        "batchId": str(batch_id or uuid4()),
        "deviceId": json["deviceId"],
        "records": [{key: value for key, value in json.items() if key != "deviceId"}],
    }
    response = await client.post(
        "/api/v1/activities/batches", headers=headers, json=batch
    )
    if response.status_code == 202:
        delivery = cast(BatchClient, client).api.state.batch_runtime.delivery
        await asyncio.wait_for(delivery.wait_idle(), 10)
        if expected_failure is not None:
            failures = [
                value
                for value in delivery.inspect().values()
                if value.message.batch_id == UUID(batch["batchId"])
            ]
            assert len(failures) == 1 and failures[0].error_code == expected_failure
        else:
            receipt = await receipt_for(client, response)
            assert receipt.results[0]["eventId"] == json["eventId"]
            assert receipt.results[0]["status"] in (
                {expected_result} if expected_result else {"stored", "duplicate"}
            )
    return response
