from datetime import date, datetime
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.enums import ActivityTimelineKind, SegmentType
from mosemo.activities.mappers import ActivityRecordMapper
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.schemas import (
    ActivityContext,
    ActivityCreateResponse,
    ActivityRecord,
    ActivitySegmentResponse,
    CaptureGapResponse,
    TimelineSegmentResponse,
)
from mosemo.activities.timeline import project_events
from mosemo.focus_sessions.models import FocusSessionNotFoundError
from mosemo.focus_sessions.repository import FocusSessionRepository
from mosemo.identity.public import AccountDirectory, DeviceDirectory
from mosemo.shared.application.clock import Clock
from mosemo.shared.domain.timezones import timeline_date_window


class ActivityDeviceNotFoundError(Exception):
    pass


class ActivityEventIdConflictError(Exception):
    pass


class ActivitySequenceConflictError(Exception):
    pass


context_adapter = TypeAdapter(ActivityContext)


def _timeline_response(
    segment: ActivityTimelineSegment,
    *,
    kind: ActivityTimelineKind,
    ended_at: datetime | None,
) -> TimelineSegmentResponse:
    if kind is not ActivityTimelineKind.CAPTURE_GAP:
        assert segment.last_observed_at is not None
        assert segment.context is not None
        return ActivitySegmentResponse(
            segment_id=segment.segment_id,
            segment_type=SegmentType.ACTIVITY,
            started_at=segment.started_at,
            ended_at=ended_at,
            last_observed_at=segment.last_observed_at,
            context=context_adapter.validate_python(segment.context),
        )

    assert segment.reason is not None
    return CaptureGapResponse(
        segment_id=segment.segment_id,
        segment_type=SegmentType.CAPTURE_GAP,
        started_at=segment.started_at,
        ended_at=ended_at,
        reason=segment.reason,
    )


class ActivityService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: ActivityRepository,
        devices: DeviceDirectory,
        accounts: AccountDirectory,
        clock: Clock,
    ) -> None:
        self._session = session
        self._repository = repository
        self._devices = devices
        self._accounts = accounts
        self._clock = clock
        self._mapper = ActivityRecordMapper()

    async def create_activity(
        self,
        *,
        account_id: UUID,
        record: ActivityRecord,
    ) -> ActivityCreateResponse:
        now = self._clock()
        async with self._session.begin():
            await self._repository.lock_timeline(account_id)

            owned = await self._devices.find_owned(
                self._session,
                account_id=account_id,
                device_id=record.device_id,
            )
            if owned is None:
                raise ActivityDeviceNotFoundError

            if record.focus_session_id is not None:
                focus = await FocusSessionRepository(self._session).find_owned(
                    account_id, record.focus_session_id
                )
                if focus is None:
                    raise FocusSessionNotFoundError
                focus.validate_activity(
                    account_id=account_id,
                    device_id=record.device_id,
                    observed_at=record.observed_at,
                )

            candidate = self._mapper.to_entity(record)
            stored = await self._repository.add_if_absent(candidate)
            if stored is not None:
                replay_start = await self._repository.find_replay_start(
                    account_id, stored.observed_at
                )
                existing_segments = await self._repository.list_account_segments(
                    account_id, from_started_at=replay_start
                )
                records = await self._repository.list_account_records(
                    await self._devices.list_device_ids(self._session, account_id),
                    from_observed_at=replay_start,
                )
                await self._repository.replace_account_segments(
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
                return ActivityCreateResponse(
                    event_id=stored.event_id,
                    status="accepted",
                    received_at=stored.received_at,
                )

            existing_event = await self._repository.find_by_event_id(record.event_id)
            if existing_event is not None:
                if existing_event.has_same_content(candidate):
                    return ActivityCreateResponse(
                        event_id=existing_event.event_id,
                        status="accepted",
                        received_at=existing_event.received_at,
                    )

                raise ActivityEventIdConflictError

            existing_sequence = await self._repository.find_by_device_sequence(
                device_id=record.device_id,
                sequence=record.sequence,
            )
            if existing_sequence is not None:
                raise ActivitySequenceConflictError

            raise RuntimeError("activity insert conflict could not be resolved")

    async def get_timeline(
        self,
        *,
        account_id: UUID,
        date: date,
    ) -> list[TimelineSegmentResponse]:
        now = self._clock()
        async with self._session.begin():
            account_timezone = await self._accounts.get_timezone(
                self._session, account_id
            )
            window = timeline_date_window(
                account_timezone=account_timezone,
                requested_date=date,
                now=now,
            )
            if window is None:
                return []
            _, start, end = window
            segments = await self._repository.list_date_segments(
                account_id=account_id,
                start=start,
                end=end,
            )

            responses: list[TimelineSegmentResponse] = []
            for segment in segments:
                if not segment.overlaps_window(
                    start=start,
                    end=end,
                    now=now,
                ):
                    continue
                responses.append(
                    _timeline_response(
                        segment,
                        kind=segment.timeline_kind_at(now),
                        ended_at=segment.effective_ended_at(now),
                    )
                )
            return responses
