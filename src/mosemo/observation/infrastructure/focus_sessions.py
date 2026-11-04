from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.observation.domain.focus_sessions import FocusSession
from mosemo.observation.infrastructure.tables import focus_sessions


class FocusSessionRepository:
    async def get_or_create(
        self, db_session: AsyncSession, focus_session: FocusSession
    ) -> tuple[FocusSession, bool]:
        """Insert the session unless its ID exists; return it and whether this
        call created it. The stored session is locked `FOR UPDATE` on a conflict."""
        created = await db_session.scalar(
            insert(FocusSession)
            .values(
                id=focus_session.id,
                account_id=focus_session.account_id,
                device_id=focus_session.device_id,
                started_at=focus_session.started_at,
                target_seconds=focus_session.target_seconds,
            )
            .on_conflict_do_nothing()
            .returning(FocusSession)
        )
        if created is not None:
            return created, True
        existing = await db_session.scalar(
            select(FocusSession)
            .where(focus_sessions.c.id == focus_session.id)
            .with_for_update()
        )
        if existing is None:
            raise RuntimeError("focus session conflict could not be resolved")
        return existing, False

    async def find_owned(
        self, db_session: AsyncSession, *, account_id: UUID, focus_session_id: UUID
    ) -> FocusSession | None:
        """The account's session, locked `FOR UPDATE` until the transaction ends."""
        return await db_session.scalar(
            select(FocusSession)
            .where(
                focus_sessions.c.account_id == account_id,
                focus_sessions.c.id == focus_session_id,
            )
            .with_for_update()
        )

    async def list_completed(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        start: datetime,
        end: datetime,
    ) -> list[FocusSession]:
        result = await db_session.scalars(
            select(FocusSession)
            .where(
                focus_sessions.c.account_id == account_id,
                focus_sessions.c.ended_at.is_not(None),
                focus_sessions.c.started_at >= start,
                focus_sessions.c.started_at < end,
            )
            .order_by(focus_sessions.c.started_at.desc(), focus_sessions.c.id.desc())
        )
        return list(result.all())
