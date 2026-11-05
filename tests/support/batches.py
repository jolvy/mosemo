"""Direct batch processing for DB feature regressions, without a consumer."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, create_autospec, sentinel
from uuid import UUID, uuid4

import httpx2
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.entrypoints.api.activity_batches import (
    compose_messaging_app,
    get_batch_publisher,
)
from mosemo.entrypoints.api.config import ApiConfig
from mosemo.entrypoints.api.exception_handlers import register_exception_handlers
from mosemo.observation.application.activities import GetObservationTimeline
from mosemo.observation.application.batches import (
    AcceptActivityBatch,
    ProcessLegacyActivityBatch,
)
from mosemo.observation.domain.activities import ActivityBatchReceipt
from mosemo.observation.infrastructure.tables import activity_batch_receipts
from mosemo.observation.presentation.activities.router import create_router
from mosemo.platform.clock import utc_now
from mosemo.platform.messaging.config import BatchDeliveryConfig
from mosemo.platform.providers import get_session
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.presentation.current_account import get_current_account
from tests.support.identity import current_account_provider, token_service


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
                activity_batch_receipts.c.batch_id == UUID(response.json()["batchId"])
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


def make_app(api_config: ApiConfig):
    identity_config = api_config.identity()
    api = FastAPI()
    devices = AsyncMock()
    api.include_router(
        create_router(
            accept_activity_batch=AcceptActivityBatch(devices=devices),
            process_legacy_activity_batch=ProcessLegacyActivityBatch(
                devices=devices, dispatcher=EventDispatcher(), clock=utc_now
            ),
            get_observation_timeline=create_autospec(
                GetObservationTimeline, instance=True
            ),
            get_batch_publisher=get_batch_publisher,
        ),
        prefix="/api/v1",
    )
    register_exception_handlers(api)
    api.dependency_overrides[get_session] = lambda: sentinel.db_session
    api.dependency_overrides[get_current_account] = current_account_provider(
        identity_config
    )
    app = compose_messaging_app(api, api_config.batch_delivery)
    account_id = uuid4()
    headers = {
        "Authorization": "Bearer "
        + token_service(identity_config).issue_access_token(account_id, now=utc_now())
    }
    return app, devices, account_id, headers
