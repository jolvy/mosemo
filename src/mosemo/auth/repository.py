from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.auth.models import NativeAuthCode


class NativeAuthCodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def save(
        self,
        *,
        code_digest: str,
        account_id: UUID,
        code_challenge: str,
        expires_at: datetime,
    ) -> NativeAuthCode:
        auth_code = NativeAuthCode(
            code_digest=code_digest,
            account_id=account_id,
            code_challenge=code_challenge,
            expires_at=expires_at,
        )
        self._session.add(auth_code)
        return auth_code

    async def find_for_update(self, code_digest: str) -> NativeAuthCode | None:
        result = await self._session.scalars(
            select(NativeAuthCode)
            .where(NativeAuthCode.code_digest == code_digest)
            .with_for_update()
        )
        return result.one_or_none()

    async def delete(self, auth_code: NativeAuthCode) -> None:
        await self._session.delete(auth_code)

    async def delete_expired(self, now: datetime) -> None:
        await self._session.execute(
            delete(NativeAuthCode).where(NativeAuthCode.expires_at <= now)
        )
