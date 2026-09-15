from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.models import ActivityRecord as StoredActivityRecord
from mosemo.activities.schemas import ActivityObservation
from mosemo.activities.schemas import ActivityRecord as ActivityRecordRequest


def activity_payload(record: ActivityRecordRequest) -> dict[str, object]:
    if isinstance(record, ActivityObservation):
        return {"context": record.context.model_dump(mode="json", by_alias=True)}
    return {"state": record.state, "reason": record.reason}


def is_same_activity_record(
    stored: StoredActivityRecord,
    requested: ActivityRecordRequest,
) -> bool:
    return (
        stored.event_id == requested.event_id
        and stored.device_id == requested.device_id
        and stored.sequence == requested.sequence
        and stored.record_type == requested.record_type
        and stored.observed_at == requested.observed_at
        and stored.timezone_id == requested.timezone_id
        and stored.utc_offset_minutes == requested.utc_offset_minutes
        and stored.payload == activity_payload(requested)
    )


class ActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(
        self,
        record: ActivityRecordRequest,
    ) -> StoredActivityRecord | None:
        statement = (
            insert(StoredActivityRecord)
            .values(
                event_id=record.event_id,
                device_id=record.device_id,
                sequence=record.sequence,
                record_type=record.record_type,
                observed_at=record.observed_at,
                timezone_id=record.timezone_id,
                utc_offset_minutes=record.utc_offset_minutes,
                payload=activity_payload(record),
            )
            .on_conflict_do_nothing()
            .returning(StoredActivityRecord)
        )
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def find_by_event_id(
        self,
        event_id: UUID,
    ) -> StoredActivityRecord | None:
        return await self._session.get(StoredActivityRecord, event_id)

    async def find_by_device_sequence(
        self,
        *,
        device_id: UUID,
        sequence: int,
    ) -> StoredActivityRecord | None:
        result = await self._session.scalars(
            select(StoredActivityRecord)
            .where(StoredActivityRecord.device_id == device_id)
            .where(StoredActivityRecord.sequence == sequence)
        )
        return result.one_or_none()
