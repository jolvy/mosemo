"""Public TestBroker/plugin ASGI lifespan composition for development only."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from faststream.kafka import KafkaBroker, TestKafkaBroker
from faststream_fastapi import FastStreamAPI
from starlette.types import Receive, Scope, Send

from mosemo.activities.handler import register_activity_handler
from mosemo.config import BatchDeliveryConfig
from mosemo.infra.batch_delivery import BatchDelivery
from mosemo.middleware import BatchBodyLimitMiddleware


class DevelopmentBatchRuntime:
    def __init__(self, api: FastAPI, config: BatchDeliveryConfig):
        self.api = api
        self.config = config
        self.broker = KafkaBroker(logger=None)
        self.handler = register_activity_handler(self.broker)
        self.delivery = BatchDelivery(self.broker, capacity=config.capacity)
        api.state.batch_delivery = self.delivery
        api.add_middleware(BatchBodyLimitMiddleware)
        self.plugin = FastStreamAPI(self.broker, application=api, safe_connection=True)
        self.running_app = None

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[dict]:
        if not self.config.enabled:
            async with self.api.router.lifespan_context(self.api) as state:
                yield dict(state or {})
            return
        async with (
            TestKafkaBroker(self.broker, connect_only=True),
            LifespanManager(self.plugin, shutdown_timeout=None) as manager,
        ):
            # TestBroker's call recorder otherwise retains every successful body.
            # Public endpoint test lifecycle method; subscriber/DI remain registered.
            self.handler.reset_test()
            self.running_app = manager.app
            try:
                async with self.delivery.lifespan():
                    yield {}
            finally:
                self.running_app = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await (self.running_app or self.plugin)(scope, receive, send)


def compose_development_app(api: FastAPI, config: BatchDeliveryConfig) -> FastAPI:
    runtime = DevelopmentBatchRuntime(api, config)
    app = FastAPI(lifespan=runtime.lifespan)
    app.mount("/", runtime)
    # HTTP is served by the inner app; export that same route schema and overrides.
    app.openapi = cast(Any, api.openapi)
    app.dependency_overrides = api.dependency_overrides
    app.state.batch_runtime = runtime
    return app
