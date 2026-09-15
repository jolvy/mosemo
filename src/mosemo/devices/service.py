from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.devices.repository import DeviceRepository
from mosemo.devices.schemas import DeviceCreateResponse


class DeviceService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: DeviceRepository,
    ) -> None:
        self._session = session
        self._repository = repository

    async def create_device(
        self,
        *,
        account_id: UUID,
        idempotency_key: UUID,
    ) -> DeviceCreateResponse:
        device = await self._repository.insert(
            account_id=account_id,
            idempotency_key=idempotency_key,
        )
        if device is not None:
            await self._session.commit()
            return DeviceCreateResponse(device_id=device.device_id)

        device = await self._repository.find_by_idempotency_key(
            account_id=account_id,
            idempotency_key=idempotency_key,
        )
        if device is None:
            await self._session.rollback()
            raise RuntimeError("device conflict could not be resolved")

        response = DeviceCreateResponse(device_id=device.device_id)
        await self._session.rollback()
        return response
