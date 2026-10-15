import asyncio
from collections.abc import AsyncIterator
from typing import Annotated
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from mosemo.activities.batch_processing import ActivityBatchProcessor
from mosemo.activities.handler import get_batch_session
from mosemo.api import v1_api_router
from mosemo.auth.tokens import TokenService
from mosemo.config import BatchDeliveryConfig, Config, get_config
from mosemo.dependencies import get_device_repository
from mosemo.exception_handlers import register_exception_handlers
from mosemo.infra.batch_runtime import compose_development_app


def payload():
    return {
        "batchId": str(uuid4()),
        "deviceId": str(uuid4()),
        "records": [
            {
                "eventId": str(uuid4()),
                "sequence": 0,
                "recordType": "activity_observation",
                "observedAt": "2026-09-14T00:00:00.123456Z",
                "timezoneId": "Asia/Seoul",
                "utcOffsetMinutes": 540,
                "context": {"kind": "opaque"},
            }
        ],
    }


def make_app(config: Config, *, capacity=64):
    api = FastAPI()
    api.include_router(v1_api_router)
    register_exception_handlers(api)
    api.dependency_overrides[get_config] = lambda: config
    devices = AsyncMock()
    api.dependency_overrides[get_device_repository] = lambda: devices
    batch_config = config.batch_delivery.model_copy(update={"capacity": capacity})
    app = compose_development_app(api, batch_config)
    account_id = uuid4()
    headers = {
        "Authorization": "Bearer "
        + TokenService(config.auth).issue_access_token(account_id)
    }
    return app, devices, account_id, headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "topic", ["activities.registration-requested", "test.custom.activities"]
)
async def test_real_subscriber_di_returns_http_202_before_held_operation_and_closes_message_session(
    config, monkeypatch, topic
):
    config.batch_delivery.topic = topic
    app, _devices, account_id, headers = make_app(config, capacity=1)
    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    session = object()
    observed = []

    async def independent_session() -> AsyncIterator[object]:
        try:
            yield session
        finally:
            closed.set()

    async def held_process(self, owner, batch, source):
        observed.append((self.session, owner, batch, source))
        entered.set()
        await release.wait()

    app.dependency_overrides[get_batch_session] = independent_session
    monkeypatch.setattr(ActivityBatchProcessor, "process", held_process)
    request = payload()
    async with (
        LifespanManager(app) as manager,
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=manager.app), base_url="http://test"
        ) as client,
    ):
        response = await asyncio.wait_for(
            client.post("/api/v1/activities/batches", headers=headers, json=request), 3
        )
        assert response.status_code == 202
        assert response.json() == {"batchId": request["batchId"], "status": "accepted"}
        await asyncio.wait_for(entered.wait(), 3)
        assert not release.is_set() and not closed.is_set()
        assert observed[0][0] is session and observed[0][1] == account_id
        assert observed[0][2].device_id == UUID(request["deviceId"])
        assert b'"records"' in observed[0][3]
        full = await client.post(
            "/api/v1/activities/batches", headers=headers, json=payload()
        )
        assert full.status_code == 503 and full.headers["retry-after"] == "1"
        release.set()
        await asyncio.wait_for(app.state.batch_runtime.delivery.wait_idle(), 3)
        assert closed.is_set()
        assert app.state.batch_runtime.delivery.inspect() == {}
    assert app.state.batch_runtime.delivery.worker.done()


@pytest.mark.asyncio
async def test_request_validation_auth_ownership_body_bytes_and_legacy_removal(config):
    app, devices, _, headers = make_app(config)
    async with (
        LifespanManager(app) as manager,
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=manager.app), base_url="http://test"
        ) as client,
    ):
        missing_auth = await client.post("/api/v1/activities/batches", json=payload())
        assert missing_auth.status_code == 401
        for invalid in [
            payload() | {"records": []},
            payload() | {"records": payload()["records"] * 201},
            payload() | {"accountId": str(uuid4())},
        ]:
            assert (
                await client.post(
                    "/api/v1/activities/batches", headers=headers, json=invalid
                )
            ).status_code == 422
        devices.find_owned_by_id.return_value = None
        assert (
            await client.post(
                "/api/v1/activities/batches", headers=headers, json=payload()
            )
        ).status_code == 404

        async def body_chunks():
            for _ in range(130):
                yield b" " * 4096

        too_large = await client.post(
            "/api/v1/activities/batches", headers=headers, content=body_chunks()
        )
        assert too_large.status_code == 413
        assert too_large.json()["error"]["status"] == "ACTIVITY_BATCH_TOO_LARGE"
        assert (
            await client.post("/api/v1/activities", headers=headers, json=payload())
        ).status_code == 404
    schema = app.openapi()
    assert "/api/v1/activities" not in schema["paths"]
    assert schema["paths"]["/api/v1/activities/batches"]["post"]["responses"]["202"]


@pytest.mark.asyncio
async def test_main_entrypoint_lifespan_preserves_http_state_and_does_not_accumulate_test_calls(
    config, monkeypatch
):
    from mosemo.dependencies import get_http_client
    from mosemo.main import create_app

    app = create_app()
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_device_repository] = lambda: AsyncMock()

    async def independent_session():
        yield object()

    app.dependency_overrides[get_batch_session] = independent_session
    processed = []

    async def process(self, owner, batch, source):
        processed.append(batch.batch_id)

    monkeypatch.setattr(ActivityBatchProcessor, "process", process)
    account = uuid4()
    headers = {
        "Authorization": "Bearer "
        + TokenService(config.auth).issue_access_token(account)
    }
    api = app.state.batch_runtime.api
    from fastapi import Depends

    @api.get("/test-http-client")
    async def http_state(
        client: Annotated[httpx2.AsyncClient, Depends(get_http_client)],
    ):
        return {"closed": client.is_closed}

    async with (
        LifespanManager(app) as manager,
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=manager.app), base_url="http://test"
        ) as client,
    ):
        assert (await client.get("/test-http-client")).json() == {"closed": False}
        assert not app.state.batch_runtime.handler.is_test
        for _ in range(70):
            assert (
                await client.post(
                    "/api/v1/activities/batches", headers=headers, json=payload()
                )
            ).status_code == 202
            await app.state.batch_runtime.delivery.wait_idle()
        assert len(processed) == 70
        assert app.state.batch_runtime.delivery.inspect() == {}
    assert app.state.batch_runtime.delivery.worker.done()


@pytest.mark.asyncio
async def test_subscriber_cancellation_runs_di_finally_before_http_resource_shutdown(
    config, monkeypatch
):
    from contextlib import asynccontextmanager

    events = []

    @asynccontextmanager
    async def resources(api):
        try:
            yield
        finally:
            events.append("http_resources_closed")

    api = FastAPI(lifespan=resources)
    api.include_router(v1_api_router)
    register_exception_handlers(api)
    api.dependency_overrides[get_config] = lambda: config
    api.dependency_overrides[get_device_repository] = lambda: AsyncMock()
    entered = asyncio.Event()

    async def session():
        try:
            yield object()
        finally:
            events.append("message_session_closed")

    api.dependency_overrides[get_batch_session] = session

    async def held(self, owner, batch, source):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ActivityBatchProcessor, "process", held)
    app = compose_development_app(api, BatchDeliveryConfig())
    headers = {
        "Authorization": "Bearer "
        + TokenService(config.auth).issue_access_token(uuid4())
    }
    async with (
        LifespanManager(app) as manager,
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=manager.app), base_url="http://test"
        ) as client,
    ):
        assert (
            await client.post(
                "/api/v1/activities/batches", headers=headers, json=payload()
            )
        ).status_code == 202
        await asyncio.wait_for(entered.wait(), 3)
    assert events == ["message_session_closed", "http_resources_closed"]
    assert app.state.batch_runtime.delivery.worker.done()


@pytest.mark.asyncio
async def test_startup_resource_failure_starts_no_worker(config):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def broken(api):
        raise RuntimeError("controlled startup failure")
        yield

    api = FastAPI(lifespan=broken)
    app = compose_development_app(api, BatchDeliveryConfig())
    with pytest.raises(RuntimeError, match="controlled startup failure"):
        async with LifespanManager(app):
            pass
    assert app.state.batch_runtime.delivery.worker is None
    assert not app.state.batch_runtime.delivery.accepting


@pytest.mark.asyncio
async def test_shutdown_resource_error_still_stops_and_awaits_worker():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def broken_shutdown(api):
        try:
            yield
        finally:
            raise RuntimeError("controlled shutdown failure")

    app = compose_development_app(
        FastAPI(lifespan=broken_shutdown), BatchDeliveryConfig()
    )
    with pytest.raises(RuntimeError, match="controlled shutdown failure"):
        async with LifespanManager(app):
            assert app.state.batch_runtime.delivery.accepting
    assert not app.state.batch_runtime.delivery.accepting
    assert app.state.batch_runtime.delivery.worker.done()
