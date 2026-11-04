from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.domain.devices import Device
from mosemo.identity.infrastructure.tables import devices


class DeviceRepository:
    async def get_or_create(
        self, db_session: AsyncSession, device: Device
    ) -> tuple[Device, bool]:
        """Insert the device unless the account already used the idempotency key;
        return it and whether this call created it."""
        created = await db_session.scalar(
            insert(Device)
            .values(
                id=device.id,
                account_id=device.account_id,
                idempotency_key=device.idempotency_key,
            )
            .on_conflict_do_nothing(
                index_elements=[devices.c.account_id, devices.c.idempotency_key]
            )
            .returning(Device)
        )
        if created is not None:
            return created, True
        existing = await db_session.scalar(
            select(Device)
            .where(devices.c.account_id == device.account_id)
            .where(devices.c.idempotency_key == device.idempotency_key)
        )
        if existing is None:
            raise RuntimeError("device conflict could not be resolved")
        return existing, False
