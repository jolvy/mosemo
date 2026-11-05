"""Observation test helpers: the old single-record service calls over the use cases."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.entrypoints.consumer.activity_batches import BatchProcessingExecutor
from mosemo.identity.bootstrap import create_device_directory
from mosemo.identity.public import AccountDirectory, DeviceDirectory
from mosemo.observation.application.activities import (
    GetObservationTimeline,
    RecordActivity,
)
from mosemo.observation.bootstrap import (
    create_observation_timeline,
    create_process_activity_batch,
)
from mosemo.observation.presentation.activities.mappers import (
    ActivityRecordMapper,
    timeline_segment_response,
)
from mosemo.observation.presentation.activities.schemas import (
    ActivityCreateResponse,
    ActivityRecord,
    TimelineSegmentResponse,
)
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher


class ActivityService:
    """Records one activity and reads the timeline through the observation use
    cases, mapping requests and results as the removed activity service did."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        devices: DeviceDirectory,
        accounts: AccountDirectory,
        clock: Clock,
    ) -> None:
        self._session = session
        self._record_activity = RecordActivity(
            devices=devices, dispatcher=EventDispatcher(), clock=clock
        )
        self._get_timeline = GetObservationTimeline(
            timeline=create_observation_timeline(), accounts=accounts, clock=clock
        )

    async def create_activity(
        self, *, account_id: UUID, record: ActivityRecord
    ) -> ActivityCreateResponse:
        stored = await self._record_activity.execute(
            self._session,
            account_id=account_id,
            record=ActivityRecordMapper().to_entity(record),
        )
        return ActivityCreateResponse.from_record(stored)

    async def get_timeline(
        self, *, account_id: UUID, date: date
    ) -> list[TimelineSegmentResponse]:
        segments = await self._get_timeline.execute(
            self._session, account_id=account_id, day=date
        )
        return [timeline_segment_response(segment) for segment in segments]


def batch_executor(
    sessions: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    *,
    clock: Clock,
) -> BatchProcessingExecutor:
    """The Consumer's executor over the batch use case, as `run_consumer` builds it."""
    return BatchProcessingExecutor(
        sessions,
        process_batch=create_process_activity_batch(
            devices=create_device_directory(),
            dispatcher=EventDispatcher(),
            clock=clock,
        ),
    )
