from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.focus_sessions.models import FocusSession


class FocusSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_if_absent(self, focus_session: FocusSession) -> FocusSession | None:
        result = await self._session.scalars(
            insert(FocusSession)
            .values(
                session_id=focus_session.session_id,
                account_id=focus_session.account_id,
                device_id=focus_session.device_id,
                started_at=focus_session.started_at,
                target_seconds=focus_session.target_seconds,
            )
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
        self, account_id: UUID, start: datetime, *, end: datetime
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
