from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.domain.devices import Device
from mosemo.identity.infrastructure.devices import DeviceRepository
from mosemo.identity.infrastructure.queries import DeviceQuery
from mosemo.infra.transactions import unit_of_work
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.application.use_case import UseCase


class RegisterDevice(UseCase):
    def __init__(
        self, *, devices: DeviceRepository, dispatcher: EventDispatcher
    ) -> None:
        self._devices = devices
        self._dispatcher = dispatcher

    async def execute(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        idempotency_key: UUID,
    ) -> UUID:
        """Register the device once per account and idempotency key."""
        async with unit_of_work(db_session, self._dispatcher):
            device, _ = await self._devices.get_or_create(
                db_session,
                Device(account_id=account_id, idempotency_key=idempotency_key),
            )
        return device.id


class DeviceDirectory:
    """Device ownership for other contexts inside their use case transaction."""

    def __init__(self, *, devices: DeviceQuery) -> None:
        self._devices = devices

    async def find_owned(
        self, db_session: AsyncSession, *, account_id: UUID, device_id: UUID
    ) -> UUID | None:
        """Return the device ID when the account owns the device."""
        return await self._devices.find_owned_id(
            db_session, account_id=account_id, device_id=device_id
        )

    async def list_device_ids(
        self, db_session: AsyncSession, account_id: UUID
    ) -> list[UUID]:
        return await self._devices.list_ids(db_session, account_id)
