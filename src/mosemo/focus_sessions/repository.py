from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.focus_sessions.models import FocusSession
from mosemo.focus_sessions.schemas import FocusSessionCreateRequest


class FocusSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, account_id: UUID, request: FocusSessionCreateRequest
    ) -> FocusSession | None:
        result = await self._session.scalars(
            insert(FocusSession)
            .values(account_id=account_id, **request.model_dump())
            .on_conflict_do_nothing()
            .returning(FocusSession)
        )
        return result.one_or_none()

    async def find_owned(
        self, account_id: UUID, session_id: UUID
    ) -> FocusSession | None:
        return await self._session.scalar(
            select(FocusSession)
            .where(
                FocusSession.account_id == account_id,
                FocusSession.session_id == session_id,
            )
            .with_for_update()
        )

    async def list_completed(
        self, account_id: UUID, start: datetime, end: datetime
    ) -> list[FocusSession]:
        result = await self._session.scalars(
            select(FocusSession)
            .where(
                FocusSession.account_id == account_id,
                FocusSession.ended_at.is_not(None),
                FocusSession.started_at >= start,
                FocusSession.started_at < end,
            )
            .order_by(FocusSession.started_at.desc(), FocusSession.session_id.desc())
        )
        return list(result.all())
