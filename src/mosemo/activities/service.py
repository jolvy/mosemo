from datetime import UTC, date, datetime, time, timedelta
from hashlib import blake2b
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

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
            try:
                await self._session.execute(text("SET LOCAL lock_timeout = '3s'"))
                await self._session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": activity_timeline_lock_key(account_id)},
                )
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) == "55P03":
                    raise ActivityTimelineBusyError from exc
                raise

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
            timezone_name = await self._repository.find_account_timezone(account_id)
            if timezone_name is None:
                raise ActivityAccountNotFoundError
            timezone = ZoneInfo(timezone_name)
            today = current_time.astimezone(timezone).date()
            if date > today:
                return []
            try:
                start = datetime.combine(date, time.min, tzinfo=timezone).astimezone(
                    UTC
                )
            except OverflowError:
                start = datetime.min.replace(tzinfo=UTC)
            end = datetime.combine(
                date + timedelta(days=1), time.min, tzinfo=timezone
            ).astimezone(UTC)
            segments = await self._repository.list_date_segments(
                account_id=account_id,
                start=start,
                end=end,
            )

            responses: list[TimelineSegmentResponse] = []
            for segment in segments:
                ended_at = segment.ended_at
                if segment.segment_type == "activity" and ended_at is None:
                    last_observed_at = segment.last_observed_at
                    assert last_observed_at is not None
                    if current_time - last_observed_at > MAX_OBSERVATION_GAP:
                        ended_at = last_observed_at
                if ended_at is None and segment.segment_type == "activity":
                    if not (segment.started_at < end and last_observed_at >= start):
                        continue
                elif ended_at is None:
                    if date > today or segment.started_at >= end:
                        continue
                elif ended_at == segment.started_at:
                    if not start <= segment.started_at < end:
                        continue
                elif not (segment.started_at < end and ended_at > start):
                    continue

                if segment.segment_type == "activity":
                    assert segment.last_observed_at is not None
                    assert segment.context is not None
                    responses.append(
                        ActivitySegmentResponse(
                            segment_id=segment.segment_id,
                            segment_type="activity",
                            started_at=segment.started_at,
                            ended_at=ended_at,
                            last_observed_at=segment.last_observed_at,
                            context=context_adapter.validate_python(segment.context),
                        )
                    )
                else:
                    assert segment.reason is not None
                    responses.append(
                        CaptureGapResponse(
                            segment_id=segment.segment_id,
                            segment_type="capture_gap",
                            started_at=segment.started_at,
                            ended_at=ended_at,
                            reason=segment.reason,
                        )
                    )
            return responses
