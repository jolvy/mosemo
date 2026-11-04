"""Publish activity batch messages from the API process lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI

from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.activities.schemas import ActivityBatchRequest
from mosemo.api.middleware import BatchBodyLimitMiddleware
from mosemo.infra.config import BatchDeliveryConfig
from mosemo.infra.messaging.publisher import KafkaPublisher
from mosemo.infra.messaging.runtime import kafka_publisher


class ActivityBatchPublisher:
    def __init__(self, publisher: KafkaPublisher) -> None:
        self.publisher = publisher

    async def publish(self, account_id: UUID, batch: ActivityBatchRequest) -> None:
        message = ActivityRegistrationRequested.from_batch(account_id, batch)
        await self.publisher.publish(
            key=str(account_id).encode("utf-8"),
            value=message.model_dump_json(by_alias=True).encode("utf-8"),
        )


class MessagingRuntime:
    def __init__(self, api: FastAPI, config: BatchDeliveryConfig):
        self.api = api
        self.config = config
        self._api_lifespan = api.router.lifespan_context

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[dict]:
        app.state.batch_publisher = None
        async with self._api_lifespan(app) as state:
            if not self.config.enabled:
                yield dict(state or {})
                return
            async with kafka_publisher(
                self.config.bootstrap_servers, topic=self.config.topic
            ) as publisher:
                app.state.batch_publisher = ActivityBatchPublisher(publisher)
                try:
                    yield dict(state or {})
                finally:
                    app.state.batch_publisher = None


def compose_messaging_app(api: FastAPI, config: BatchDeliveryConfig) -> FastAPI:
    runtime = MessagingRuntime(api, config)
    api.router.lifespan_context = runtime.lifespan
    api.state.batch_runtime = runtime
    api.state.batch_publisher = None
    api.add_middleware(BatchBodyLimitMiddleware)
    return api
