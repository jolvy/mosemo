from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.repository import ActivityRepository, is_same_activity_record
from mosemo.activities.schemas import ActivityCreateResponse, ActivityRecord


class ActivityDeviceNotFoundError(Exception):
    pass


class ActivityEventIdConflictError(Exception):
    pass


class ActivitySequenceConflictError(Exception):
    pass


class ActivityService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: ActivityRepository,
    ) -> None:
        self._session = session
        self._repository = repository

    async def create_activity(
        self,
        *,
        account_id: UUID,
        record: ActivityRecord,
    ) -> ActivityCreateResponse:
        device = await self._repository.find_owned_device(
            account_id=account_id,
            device_registration_id=record.device_registration_id,
        )
        if device is None:
            raise ActivityDeviceNotFoundError

        stored = await self._repository.insert(record)
        if stored is not None:
            await self._session.commit()
            return ActivityCreateResponse(
                event_id=stored.event_id,
                status="accepted",
                received_at=stored.received_at,
            )

        existing_event = await self._repository.find_by_event_id(record.event_id)
        if existing_event is not None:
            if is_same_activity_record(existing_event, record):
                response = ActivityCreateResponse(
                    event_id=existing_event.event_id,
                    status="accepted",
                    received_at=existing_event.received_at,
                )
                return response

            raise ActivityEventIdConflictError

        existing_sequence = await self._repository.find_by_device_sequence(
            device_registration_id=record.device_registration_id,
            sequence=record.sequence,
        )
        if existing_sequence is not None:
            raise ActivitySequenceConflictError

        raise RuntimeError("activity insert conflict could not be resolved")
