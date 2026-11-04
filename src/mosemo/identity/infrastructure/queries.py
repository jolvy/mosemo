from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.infrastructure.tables import accounts, devices
from mosemo.shared.domain.timezones import Timezone


class AccountQuery:
    async def exists(self, db_session: AsyncSession, account_id: UUID) -> bool:
        return bool(
            await db_session.scalar(select(exists().where(accounts.c.id == account_id)))
        )

    async def find_timezone(
        self, db_session: AsyncSession, account_id: UUID
    ) -> Timezone | None:
        return await db_session.scalar(
            select(accounts.c.timezone).where(accounts.c.id == account_id)
        )


class DeviceQuery:
    async def find_owned_id(
        self, db_session: AsyncSession, *, account_id: UUID, device_id: UUID
    ) -> UUID | None:
        return await db_session.scalar(
            select(devices.c.id)
            .where(devices.c.id == device_id)
            .where(devices.c.account_id == account_id)
        )

    async def list_ids(self, db_session: AsyncSession, account_id: UUID) -> list[UUID]:
        result = await db_session.scalars(
            select(devices.c.id)
            .where(devices.c.account_id == account_id)
            .order_by(devices.c.id)
        )
        return list(result.all())
