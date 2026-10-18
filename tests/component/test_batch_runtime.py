import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from mosemo.activities.batch_processing import ActivityBatchProcessor
from mosemo.api import v1_api_router
from mosemo.auth.tokens import TokenService
from mosemo.config import BatchDeliveryConfig, Config, get_config
from mosemo.dependencies import get_device_repository
from mosemo.exception_handlers import register_exception_handlers
from mosemo.infra.batch_runtime import compose_messaging_app
from tests.component.test_activities_api import activity_request as payload


def make_app(config: Config):
    api = FastAPI()
    api.include_router(v1_api_router)
    register_exception_handlers(api)
    api.dependency_overrides[get_config] = lambda: config
    devices = AsyncMock()
    api.dependency_overrides[get_device_repository] = lambda: devices
    app = compose_messaging_app(api, config.batch_delivery)
    account_id = uuid4()
    headers = {
        "Authorization": "Bearer "
        + TokenService(config.auth).issue_access_token(account_id)
    }
    return app, devices, account_id, headers


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "startup", "shutdown", "cancel"])
async def test_api_lifecycle_closes_producer_and_preserves_inner_state(
    monkeypatch, failure
):
    events = []

    @asynccontextmanager
    async def inner(api):
        events.append("inner-start")
        try:
            yield {"http_client": "preserved"}
        finally:
            events.append("inner-stop")
            if failure == "shutdown":
                raise RuntimeError("shutdown")

    producer = AsyncMock()
    if failure == "startup":
        producer.start.side_effect = RuntimeError("startup")
    constructor = Mock(return_value=producer)
    monkeypatch.setattr("mosemo.infra.batch_runtime.AIOKafkaProducer", constructor)
    config = BatchDeliveryConfig(bootstrap_servers="test:9092", topic="custom")
    app = compose_messaging_app(FastAPI(lifespan=inner), config)
    try:
        async with app.router.lifespan_context(app) as state:
            assert state is not None and state["http_client"] == "preserved"
            assert app.state.batch_publisher.topic == "custom"
            producer.start.assert_awaited_once()
            if failure == "cancel":
                raise asyncio.CancelledError
    except RuntimeError, asyncio.CancelledError:
        assert failure is not None
    else:
        assert failure is None
    assert app.state.batch_publisher is None
    producer.stop.assert_awaited_once()
    constructor.assert_called_once_with(
        bootstrap_servers="test:9092", acks="all", enable_idempotence=True
    )
    assert events == ["inner-start", "inner-stop"]


@pytest.mark.asyncio
async def test_cancel_during_producer_start_closes_connection(monkeypatch):
    producer = AsyncMock()
    entered = asyncio.Event()

    async def held():
        entered.set()
        await asyncio.Event().wait()

    producer.start.side_effect = held
    monkeypatch.setattr(
        "mosemo.infra.batch_runtime.AIOKafkaProducer", Mock(return_value=producer)
    )
    app = compose_messaging_app(FastAPI(), BatchDeliveryConfig())

    async def run():
        async with app.router.lifespan_context(app):
            pytest.fail("startup must not complete")

    pending = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    producer.stop.assert_awaited_once()
    assert app.state.batch_publisher is None


@pytest.mark.asyncio
async def test_disabled_publication_still_runs_api_lifecycle(config, monkeypatch):
    config.batch_delivery.enabled = False
    app, _, _, headers = make_app(config)
    constructor = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr("mosemo.infra.batch_runtime.AIOKafkaProducer", constructor)
    async with (
        LifespanManager(app) as manager,
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=manager.app), base_url="http://test"
        ) as client,
    ):
        response = await client.post(
            "/api/v1/activities/batches", headers=headers, json=payload()
        )
        assert response.status_code == 503
        assert response.headers["retry-after"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery_enabled", [True, False])
async def test_legacy_waits_for_processing_without_admission_or_publish(
    config, monkeypatch, delivery_enabled
):
    from mosemo.database import get_session

    config.batch_delivery.enabled = delivery_enabled
    app, _, owner, headers = make_app(config)
    session = object()
    app.dependency_overrides[get_session] = lambda: session
    entered, release = asyncio.Event(), asyncio.Event()
    source = payload()

    async def held_process(self, account_id, batch):
        assert self.session is session
        assert account_id == owner
        assert str(batch.batch_id) == source["batchId"]
        entered.set()
        await release.wait()
        return [{"eventId": source["records"][0]["eventId"], "status": "stored"}]

    monkeypatch.setattr(ActivityBatchProcessor, "process", held_process)
    publish = AsyncMock(side_effect=AssertionError("must not publish"))
    monkeypatch.setattr(app.state, "batch_publisher", AsyncMock(publish=publish))
    async with (
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        pending = asyncio.create_task(
            client.post(
                "/api/v1/activities/batches/legacy", headers=headers, json=source
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert not pending.done()
        finally:
            release.set()
        response = await asyncio.wait_for(pending, 3)
        assert response.status_code == 200
        assert response.json() == {
            "batchId": source["batchId"],
            "status": "processed",
            "results": [
                {"eventId": source["records"][0]["eventId"], "status": "stored"}
            ],
        }
        publish.assert_not_called()
    responses = app.openapi()["paths"]["/api/v1/activities/batches/legacy"]["post"][
        "responses"
    ]
    assert responses["503"]["headers"]["Retry-After"]["schema"]["const"] == "1"


@pytest.mark.asyncio
async def test_legacy_auth_validation_and_body_limit(config, monkeypatch):
    from mosemo.database import get_session

    app, _, _, headers = make_app(config)
    app.dependency_overrides[get_session] = object
    process = AsyncMock()
    monkeypatch.setattr(ActivityBatchProcessor, "process", process)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = "/api/v1/activities/batches/legacy"
        assert (await client.post(path, json=payload())).status_code == 401
        assert (
            await client.post(path, headers=headers, json=payload() | {"records": []})
        ).status_code == 422
        assert (
            await client.post(path, headers=headers, content=b" " * (512 * 1024 + 1))
        ).status_code == 413
    process.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,expected",
    [
        ("device_not_owned", 404),
        ("batch_content_conflict", 409),
        ("projection_time_regression", 422),
        ("FocusSessionNotFoundError", 404),
        ("FocusSessionInvalidTimeError", 409),
        ("busy", 503),
    ],
)
async def test_legacy_processing_errors(config, monkeypatch, error, expected):
    from mosemo.activities.messages import TerminalBatchError
    from mosemo.activities.service import ActivityTimelineBusyError
    from mosemo.database import get_session

    app, _, _, headers = make_app(config)
    app.dependency_overrides[get_session] = object
    failure = (
        ActivityTimelineBusyError() if error == "busy" else TerminalBatchError(error)
    )
    monkeypatch.setattr(
        ActivityBatchProcessor, "process", AsyncMock(side_effect=failure)
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/activities/batches/legacy", headers=headers, json=payload()
        )
    assert response.status_code == expected
    if error == "busy":
        assert response.headers["retry-after"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connection", "timeout"])
async def test_http_maps_publisher_failure_to_retryable_503(config, failure):
    from aiokafka.errors import KafkaConnectionError

    from mosemo.infra.batch_delivery import ActivityBatchPublisher

    app, _, _, headers = make_app(config)
    producer = AsyncMock()
    cleaned = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    producer.send_and_wait.side_effect = (
        KafkaConnectionError() if failure == "connection" else stalled
    )
    app.state.batch_publisher = ActivityBatchPublisher(
        producer, topic="test", acknowledgment_timeout=0.02
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await asyncio.wait_for(
            client.post("/api/v1/activities/batches", headers=headers, json=payload()),
            1,
        )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"]["status"] == "ACTIVITY_DELIVERY_UNAVAILABLE"
    if failure == "timeout":
        assert cleaned.is_set()
