"""Compose messaging resources for the separate API and Consumer processes."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiokafka import AIOKafkaProducer
from fastapi import FastAPI
from faststream.kafka import KafkaBroker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.activities.executor import BatchProcessingExecutor
from mosemo.config import BatchDeliveryConfig
from mosemo.infra.batch_consumer import ActivityBatchConsumer
from mosemo.infra.batch_delivery import ActivityBatchPublisher
from mosemo.middleware import BatchBodyLimitMiddleware


class MessagingRuntime:
    @staticmethod
    async def run_consumer(
        config: BatchDeliveryConfig,
        sessions: async_sessionmaker[AsyncSession],
        stop: asyncio.Event,
    ) -> None:
        if not config.enabled:
            raise ValueError("Activity Consumer requires BATCH_ENABLED=true")
        broker = KafkaBroker(config.bootstrap_servers, logger=None, graceful_timeout=0)
        await ActivityBatchConsumer(broker, BatchProcessingExecutor(sessions)).run(
            config.topic, stop, group_id=config.group_id
        )

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
            producer = AIOKafkaProducer(
                bootstrap_servers=self.config.bootstrap_servers,
                acks="all",
                enable_idempotence=True,
            )
            try:
                await producer.start()
                app.state.batch_publisher = ActivityBatchPublisher(
                    producer,
                    topic=self.config.topic,
                )
                yield dict(state or {})
            finally:
                app.state.batch_publisher = None
                await producer.stop()


def compose_messaging_app(api: FastAPI, config: BatchDeliveryConfig) -> FastAPI:
    runtime = MessagingRuntime(api, config)
    api.router.lifespan_context = runtime.lifespan
    api.state.batch_runtime = runtime
    api.state.batch_publisher = None
    api.add_middleware(BatchBodyLimitMiddleware)
    return api
