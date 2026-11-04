from datetime import date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.public import AccountDirectory, DeviceDirectory
from mosemo.infra.transactions import read_snapshot, unit_of_work
from mosemo.observation.application.focus_sessions import FocusSessionLinks
from mosemo.observation.application.timeline import (
    ObservationTimeline,
    ObservedSegment,
)
from mosemo.observation.domain.activities import (
    ActivityDeviceNotFoundError,
    ActivityEventIdConflictError,
    ActivityRecord,
    ActivitySequenceConflictError,
)
from mosemo.observation.domain.timeline import project_events
from mosemo.observation.infrastructure.activity_records import (
    ActivityRecordRepository,
)
from mosemo.observation.infrastructure.focus_sessions import FocusSessionRepository
from mosemo.observation.infrastructure.timeline_segments import (
    TimelineSegmentRepository,
)
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.application.use_case import UseCase
from mosemo.shared.domain.timezones import timeline_date_window


class RecordActivity(UseCase):
    def __init__(
        self, *, devices: DeviceDirectory, dispatcher: EventDispatcher, clock: Clock
    ) -> None:
        self._records = ActivityRecordRepository()
        self._segments = TimelineSegmentRepository()
        self._focus_sessions = FocusSessionLinks(
            focus_sessions=FocusSessionRepository()
        )
        self._devices = devices
        self._dispatcher = dispatcher
        self._clock = clock

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, record: ActivityRecord
    ) -> ActivityRecord:
        """Store one record and project it, or return the identical stored record.

        Raises `ActivityDeviceNotFoundError`, the focus session errors,
        `ActivityEventIdConflictError` for the same event ID with other content,
        and `ActivitySequenceConflictError` for a device sequence taken by
        another event.
        """
        now = self._clock()
        async with unit_of_work(db_session, self._dispatcher):
            await self._segments.lock_timeline(db_session, account_id)
            owned = await self._devices.find_owned(
                db_session, account_id=account_id, device_id=record.device_id
            )
            if owned is None:
                raise ActivityDeviceNotFoundError
            await self._focus_sessions.validate(
                db_session, account_id=account_id, record=record
            )
            stored, created = await self._records.get_or_create(db_session, record)
            if not created:
                if stored.event_id != record.event_id:
                    raise ActivitySequenceConflictError
                if not stored.has_same_content(record):
                    raise ActivityEventIdConflictError
                return stored
            await self._project(
                db_session, account_id=account_id, stored=stored, now=now
            )
        return stored

    async def _project(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        stored: ActivityRecord,
        now: datetime,
    ) -> None:
        replay_start = await self._segments.find_replay_start(
            db_session, account_id, observed_at=stored.observed_at
        )
        existing_segments = await self._segments.list_account_segments(
            db_session, account_id, from_started_at=replay_start
        )
        records = await self._records.list_account_records(
            db_session,
            await self._devices.list_device_ids(db_session, account_id),
            from_observed_at=replay_start,
        )
        await self._segments.replace_account_segments(
            db_session,
            account_id=account_id,
            projected=project_events(
                records,
                now=now,
                stop_after_event_id=stored.event_id,
                stop_when_closed=lambda segment: (
                    segment.converged_index(
                        existing_segments, observed_at=stored.observed_at
                    )
                    is not None
                ),
            ),
            existing=existing_segments,
            event_observed_at=stored.observed_at,
        )


class GetObservationTimeline(UseCase):
    def __init__(
        self,
        *,
        timeline: ObservationTimeline,
        accounts: AccountDirectory,
        clock: Clock,
    ) -> None:
        self._timeline = timeline
        self._accounts = accounts
        self._clock = clock

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, day: date
    ) -> list[ObservedSegment]:
        """The whole observation timeline of the account's calendar day, uncut.

        Raises identity's `AccountNotFoundError` when the account does not exist.
        """
        now = self._clock()
        async with read_snapshot(db_session):
            account_timezone = await self._accounts.get_timezone(db_session, account_id)
            window = timeline_date_window(
                account_timezone=account_timezone, requested_date=day, now=now
            )
            if window is None:
                return []
            _, start, end = window
            return await self._timeline.list_window(
                db_session, account_id=account_id, start=start, end=end, now=now
            )
