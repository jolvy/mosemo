from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.devices.models import Device


class DeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_owned_by_id(
        self,
        *,
        account_id: UUID,
        device_id: UUID,
    ) -> Device | None:
        result = await self._session.scalars(
            select(Device)
            .where(Device.device_id == device_id)
            .where(Device.account_id == account_id)
        )
        return result.one_or_none()

    async def insert(
        self,
        *,
        account_id: UUID,
        idempotency_key: UUID,
    ) -> Device | None:
        statement = (
            insert(Device)
            .values(account_id=account_id, idempotency_key=idempotency_key)
            .on_conflict_do_nothing(
                index_elements=[Device.account_id, Device.idempotency_key]
            )
            .returning(Device)
        )
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def find_by_idempotency_key(
        self,
        *,
        account_id: UUID,
        idempotency_key: UUID,
    ) -> Device | None:
        result = await self._session.scalars(
            select(Device)
            .where(Device.account_id == account_id)
            .where(Device.idempotency_key == idempotency_key)
        )
        return result.one_or_none()
