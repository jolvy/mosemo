import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, create_autospec, sentinel
from uuid import uuid4

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

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
from mosemo.observation.presentation.activities.router import create_router
from mosemo.platform.clock import utc_now
from mosemo.platform.messaging.config import BatchDeliveryConfig
from mosemo.platform.providers import get_session
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.presentation.current_account import get_current_account
from tests.component.test_activities_api import activity_request as payload
from tests.support.identity import current_account_provider, token_service
from tests.support.use_cases import record_transactions


@pytest.fixture(autouse=True)
def no_database_transactions(monkeypatch: pytest.MonkeyPatch) -> None:
    # The device check runs on the mocked directory; no database is involved.
    record_transactions(
        monkeypatch, "mosemo.observation.application.batches", "read_snapshot"
    )


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
    monkeypatch.setattr("mosemo.platform.messaging.runtime.KafkaBroker", constructor)
    config = BatchDeliveryConfig(bootstrap_servers="test:9092", topic="custom")
    app = compose_messaging_app(FastAPI(lifespan=inner), config)
    try:
        async with app.router.lifespan_context(app) as state:
            assert state is not None
            assert state["http_client"] == "preserved"
            assert app.state.batch_publisher.publisher.topic == "custom"
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
        "test:9092", logger=None, acks="all", enable_idempotence=True
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
        "mosemo.platform.messaging.runtime.KafkaBroker", Mock(return_value=producer)
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
async def test_disabled_publication_still_runs_api_lifecycle(api_config, monkeypatch):
    api_config.batch_delivery.enabled = False
    app, _, _, headers = make_app(api_config)
    constructor = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr("mosemo.platform.messaging.runtime.KafkaBroker", constructor)
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
    api_config, monkeypatch, delivery_enabled
):
    from mosemo.platform.database.engine import get_session

    api_config.batch_delivery.enabled = delivery_enabled
    app, _, owner, headers = make_app(api_config)
    session = object()
    app.dependency_overrides[get_session] = lambda: session
    entered, release = asyncio.Event(), asyncio.Event()
    source = payload()

    async def held_process(self, db_session, *, account_id, batch):
        assert db_session is session
        assert account_id == owner
        assert str(batch.batch_id) == source["batchId"]
        entered.set()
        await release.wait()
        return [{"eventId": source["records"][0]["eventId"], "status": "stored"}]

    monkeypatch.setattr(ProcessLegacyActivityBatch, "execute", held_process)
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
async def test_legacy_auth_validation_and_body_limit(api_config, monkeypatch):
    from mosemo.platform.database.engine import get_session

    app, _, _, headers = make_app(api_config)
    app.dependency_overrides[get_session] = object
    process = AsyncMock()
    monkeypatch.setattr(ProcessLegacyActivityBatch, "execute", process)
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
    ("error", "expected"),
    [
        ("device_not_owned", 404),
        ("batch_content_conflict", 409),
        ("projection_time_regression", 422),
        ("FocusSessionNotFoundError", 404),
        ("FocusSessionInvalidTimeError", 409),
        ("busy", 503),
    ],
)
async def test_legacy_processing_errors(api_config, monkeypatch, error, expected):
    from mosemo.observation.application.batches import TerminalBatchError
    from mosemo.observation.domain.timeline import ActivityTimelineBusyError
    from mosemo.platform.database.engine import get_session

    app, _, _, headers = make_app(api_config)
    app.dependency_overrides[get_session] = object
    failure = (
        ActivityTimelineBusyError() if error == "busy" else TerminalBatchError(error)
    )
    monkeypatch.setattr(
        ProcessLegacyActivityBatch, "execute", AsyncMock(side_effect=failure)
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
async def test_http_maps_publisher_failure_to_retryable_503(api_config, failure):
    from aiokafka.errors import KafkaConnectionError

    from mosemo.entrypoints.api.activity_batches import ActivityBatchPublisher
    from mosemo.platform.messaging.publisher import KafkaPublisher

    app, _, _, headers = make_app(api_config)
    producer = AsyncMock()
    cleaned = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    producer.publish.side_effect = (
        KafkaConnectionError() if failure == "connection" else stalled
    )
    app.state.batch_publisher = ActivityBatchPublisher(
        KafkaPublisher(producer, topic="test", acknowledgment_timeout=0.02)
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
