"""Explicit FastStream boundary; each message owns a fresh DB session."""

import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Body, Depends, Header
from faststream.kafka import KafkaBroker
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.batch_processing import ActivityBatchProcessor
from mosemo.activities.schemas import ActivityBatchRequest
from mosemo.activities.service import ActivityTimelineBusyError
from mosemo.database import SessionFactory


class RegistrationSnapshot(BaseModel):
    """Internal UTF-8 byte snapshot transported losslessly as a JSON string."""

    source: str


async def get_batch_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


def register_activity_handler(broker: KafkaBroker, *, topic: str):
    @broker.subscriber(topic)
    async def register_requested(
        snapshot: Annotated[RegistrationSnapshot, Body()],
        account_id: Annotated[UUID, Header(alias="x-account-id")],
        session: Annotated[AsyncSession, Depends(get_batch_session)],
    ) -> None:
        batch = ActivityBatchRequest.model_validate(json.loads(snapshot.source))
        try:
            await ActivityBatchProcessor(session).process(account_id, batch)
        except ActivityTimelineBusyError:
            raise ConnectionError("activity_timeline_lock_busy") from None

    return register_requested
