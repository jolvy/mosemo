from datetime import UTC, date, datetime, time, timedelta
from hashlib import blake2b
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.enums import SegmentType
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.repository import (
    ActivityRepository,
    converged_old_index,
    is_same_activity_record,
)
from mosemo.activities.schemas import (
    ActivityContext,
    ActivityCreateResponse,
    ActivityRecord,
    ActivitySegmentResponse,
    CaptureGapResponse,
    TimelineSegmentResponse,
)
from mosemo.activities.timeline import MAX_OBSERVATION_GAP, project_events
from mosemo.devices.repository import DeviceRepository
from mosemo.timezones import Timezone


class ActivityDeviceNotFoundError(Exception):
    pass


class ActivityEventIdConflictError(Exception):
    pass


class ActivitySequenceConflictError(Exception):
    pass


class ActivityTimelineBusyError(Exception):
    pass


class ActivityAccountNotFoundError(Exception):
    pass


context_adapter = TypeAdapter(ActivityContext)


def activity_timeline_lock_key(account_id: UUID) -> int:
    digest = blake2b(
        f"activity_timeline:{account_id}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, signed=True)


async def acquire_activity_timeline_lock(
    session: AsyncSession,
    *,
    account_id: UUID,
) -> None:
    try:
        await session.execute(text("SET LOCAL lock_timeout = '3s'"))
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": activity_timeline_lock_key(account_id)},
        )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise ActivityTimelineBusyError from exc
        raise


def timeline_ended_at(
    segment: ActivityTimelineSegment,
    *,
    current_time: datetime,
) -> datetime | None:
    if segment.segment_type is not SegmentType.ACTIVITY or segment.ended_at is not None:
        return segment.ended_at

    assert segment.last_observed_at is not None
    if current_time - segment.last_observed_at > MAX_OBSERVATION_GAP:
        return segment.last_observed_at
    return None


def timeline_date_window(
    *,
    account_timezone: Timezone,
    requested_date: date | None,
    current_time: datetime,
) -> tuple[date, datetime, datetime] | None:
    timezone = ZoneInfo(account_timezone.value)
    today = current_time.astimezone(timezone).date()
    selected_date = requested_date or today
    if selected_date > today:
        return None

    try:
        start = datetime.combine(
            selected_date,
            time.min,
            tzinfo=timezone,
        ).astimezone(UTC)
    except OverflowError:
        start = datetime.min.replace(tzinfo=UTC)
    end = datetime.combine(
        selected_date + timedelta(days=1),
        time.min,
        tzinfo=timezone,
    ).astimezone(UTC)
    return selected_date, start, end


def timeline_segment_overlaps_window(
    segment: ActivityTimelineSegment,
    *,
    ended_at: datetime | None,
    start: datetime,
    end: datetime,
) -> bool:
    if ended_at is None:
        if segment.segment_type is SegmentType.ACTIVITY:
            assert segment.last_observed_at is not None
            return segment.started_at < end and segment.last_observed_at >= start
        return segment.started_at < end
    if ended_at == segment.started_at:
        return start <= segment.started_at < end
    return segment.started_at < end and ended_at > start


def _timeline_response(
    segment: ActivityTimelineSegment,
    *,
    ended_at: datetime | None,
) -> TimelineSegmentResponse:
    if segment.segment_type is SegmentType.ACTIVITY:
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
        device_repository: DeviceRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._device_repository = device_repository

    async def create_activity(
        self,
        *,
        account_id: UUID,
        record: ActivityRecord,
        now: datetime | None = None,
    ) -> ActivityCreateResponse:
        current_time = now or datetime.now(UTC)
        async with self._session.begin():
            await acquire_activity_timeline_lock(
                self._session,
                account_id=account_id,
            )

            device = await self._device_repository.find_owned_by_id(
                account_id=account_id,
                device_id=record.device_id,
            )
            if device is None:
                raise ActivityDeviceNotFoundError

            stored = await self._repository.insert(record)
            if stored is not None:
                replay_start = await self._repository.find_replay_start(
                    account_id, stored.observed_at
                )
                existing_segments = await self._repository.list_account_segments(
                    account_id, from_started_at=replay_start
                )
                records = await self._repository.list_account_records(
                    account_id, from_observed_at=replay_start
                )
                await self._repository.replace_account_segments(
                    account_id=account_id,
                    projected=project_events(
                        records,
                        now=current_time,
                        stop_after_event_id=stored.event_id,
                        stop_when_closed=lambda segment: (
                            converged_old_index(
                                existing_segments, segment, stored.observed_at
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
                if is_same_activity_record(existing_event, record):
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
        now: datetime | None = None,
    ) -> list[TimelineSegmentResponse]:
        current_time = now or datetime.now(UTC)
        async with self._session.begin():
            account_timezone = await self._repository.find_account_timezone(account_id)
            if account_timezone is None:
                raise ActivityAccountNotFoundError
            window = timeline_date_window(
                account_timezone=account_timezone,
                requested_date=date,
                current_time=current_time,
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
                ended_at = timeline_ended_at(segment, current_time=current_time)
                if not timeline_segment_overlaps_window(
                    segment,
                    ended_at=ended_at,
                    start=start,
                    end=end,
                ):
                    continue
                responses.append(_timeline_response(segment, ended_at=ended_at))
            return responses
