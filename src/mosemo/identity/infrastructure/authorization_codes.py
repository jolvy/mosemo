from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.domain.authorization_codes import NativeAuthCode
from mosemo.identity.infrastructure.tables import native_auth_codes


class NativeAuthCodeRepository:
    def add(self, db_session: AsyncSession, auth_code: NativeAuthCode) -> None:
        db_session.add(auth_code)

    async def find_for_update(
        self, db_session: AsyncSession, code_digest: str
    ) -> NativeAuthCode | None:
        return await db_session.scalar(
            select(NativeAuthCode)
            .where(native_auth_codes.c.code_digest == code_digest)
            .with_for_update()
        )

    async def delete(self, db_session: AsyncSession, auth_code: NativeAuthCode) -> None:
        await db_session.delete(auth_code)

    async def delete_expired(self, db_session: AsyncSession, now: datetime) -> None:
        await db_session.execute(
            delete(native_auth_codes).where(native_auth_codes.c.expires_at <= now)
        )
