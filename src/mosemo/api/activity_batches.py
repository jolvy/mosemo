"""Publish activity batch messages from the API process lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request

from mosemo.api.middleware import BatchBodyLimitMiddleware
from mosemo.infra.config import BatchDeliveryConfig
from mosemo.infra.messaging.publisher import DeliveryUnavailableError, KafkaPublisher
from mosemo.infra.messaging.runtime import kafka_publisher
from mosemo.observation import bootstrap as observation_bootstrap
from mosemo.observation.bootstrap import (
    ActivityBatchRequest,
    ActivityDeliveryUnavailableError,
    ActivityRegistrationRequested,
)


class ActivityBatchPublisher:
    def __init__(self, publisher: KafkaPublisher) -> None:
        self.publisher = publisher

    async def publish(self, account_id: UUID, batch: ActivityBatchRequest) -> None:
        message = ActivityRegistrationRequested.from_batch(account_id, batch)
        await self.publisher.publish(
            key=str(account_id).encode("utf-8"),
            value=message.model_dump_json(by_alias=True).encode("utf-8"),
        )


class ActivityBatchDelivery:
    """The observation delivery port over the publisher in the app state."""

    def __init__(self, publisher: ActivityBatchPublisher) -> None:
        self._publisher = publisher

    async def publish(self, account_id: UUID, batch: ActivityBatchRequest) -> None:
        try:
            await self._publisher.publish(account_id, batch)
        except DeliveryUnavailableError as exc:
            raise ActivityDeliveryUnavailableError from exc


def get_batch_publisher(
    request: Request,
) -> observation_bootstrap.ActivityBatchPublisher[ActivityBatchRequest] | None:
    """The delivery port while the messaging lifespan holds a publisher."""
    publisher = getattr(request.app.state, "batch_publisher", None)
    return None if publisher is None else ActivityBatchDelivery(publisher)


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
