"""Direct batch processing for DB feature regressions, without a consumer."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID, uuid4

import httpx2
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.activities.models import ActivityBatchReceipt
from mosemo.config import BatchDeliveryConfig
from mosemo.infra.batch_runtime import compose_messaging_app


class BatchClient(httpx2.AsyncClient):
    api: FastAPI
    session_factory: async_sessionmaker[AsyncSession]


@asynccontextmanager
async def batch_client(
    api: FastAPI, factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[BatchClient]:
    app = compose_messaging_app(api, BatchDeliveryConfig(enabled=False))
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
        "/api/v1/activities/batches/legacy", headers=headers, json=batch
    )
    if expected_failure is not None:
        expected = {
            "FocusSessionInvalidTimeError": (409, "FOCUS_SESSION_CONFLICT"),
            "FocusSessionNotFoundError": (404, "FOCUS_SESSION_NOT_FOUND"),
        }[expected_failure]
        assert response.status_code == expected[0]
        assert response.json()["error"]["status"] == expected[1]
    elif response.status_code == 200:
        receipt = await receipt_for(client, response)
        assert receipt.results[0]["eventId"] == json["eventId"]
        assert receipt.results[0]["status"] in (
            {expected_result} if expected_result else {"stored", "duplicate"}
        )
    return response
