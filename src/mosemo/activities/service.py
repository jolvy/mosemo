from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.repository import ActivityRepository, is_same_activity_record
from mosemo.activities.schemas import ActivityCreateResponse, ActivityRecord
from mosemo.devices.repository import DeviceRepository


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
    ) -> ActivityCreateResponse:
        async with self._session.begin():
            device = await self._device_repository.find_owned_by_id(
                account_id=account_id,
                device_id=record.device_id,
            )
            if device is None:
                raise ActivityDeviceNotFoundError

            stored = await self._repository.insert(record)
            if stored is not None:
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
