from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.observation.domain.focus_sessions import FocusSession
from mosemo.observation.infrastructure.focus_sessions import FocusSessionRepository


class FocusSessionStatus(StrEnum):
    MISSING = "missing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class CompletedFocusSessionView:
    """A completed focus session; each pause is a `[started_at, ended_at)` span.

    `pauses_reported` is false when the client sent no pause information.
    """

    focus_session_id: UUID
    account_id: UUID
    started_at: datetime
    ended_at: datetime
    label_id: UUID
    pauses_reported: bool
    pauses: tuple[tuple[datetime, datetime], ...]


def _completed_view(session: FocusSession) -> CompletedFocusSessionView | None:
    if session.ended_at is None:
        return None
    assert session.label_id is not None
    return CompletedFocusSessionView(
        focus_session_id=session.id,
        account_id=session.account_id,
        started_at=session.started_at,
        ended_at=session.ended_at,
        label_id=session.label_id,
        pauses_reported=session.pauses_reported,
        pauses=tuple(pause.span() for pause in session.pauses),
    )


def _completed_views(sessions: list[FocusSession]) -> list[CompletedFocusSessionView]:
    return [view for session in sessions if (view := _completed_view(session))]


class FocusSessionLog:
    """Focus session reads for other contexts.

    Runs inside the caller's transaction and never opens one itself. Sessions
    of other accounts are never returned.
    """

    def __init__(self, *, focus_sessions: FocusSessionRepository) -> None:
        self._focus_sessions = focus_sessions

    async def find_status(
        self, db_session: AsyncSession, account_id: UUID, *, focus_session_id: UUID
    ) -> FocusSessionStatus:
        session = await self._focus_sessions.find_account_session(
            db_session, account_id=account_id, focus_session_id=focus_session_id
        )
        if session is None:
            return FocusSessionStatus.MISSING
        if session.ended_at is None:
            return FocusSessionStatus.IN_PROGRESS
        return FocusSessionStatus.COMPLETED

    async def find_completed(
        self, db_session: AsyncSession, account_id: UUID, *, focus_session_id: UUID
    ) -> CompletedFocusSessionView | None:
        """The account's completed session; `None` if missing or in progress."""
        session = await self._focus_sessions.find_account_session(
            db_session, account_id=account_id, focus_session_id=focus_session_id
        )
        return None if session is None else _completed_view(session)

    async def list_completed_started_between(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        start: datetime,
        end: datetime,
    ) -> list[CompletedFocusSessionView]:
        """Completed sessions with `start <= started_at < end`, earliest first."""
        latest_first = await self._focus_sessions.list_completed(
            db_session, account_id, start=start, end=end
        )
        return _completed_views(latest_first[::-1])

    async def list_completed_overlapping(
        self,
        db_session: AsyncSession,
        account_id: UUID,
        *,
        start: datetime,
        end: datetime,
    ) -> list[CompletedFocusSessionView]:
        """Completed sessions with `started_at < end` and `ended_at >= start`,
        earliest first."""
        sessions = await self._focus_sessions.list_completed_overlapping(
            db_session, account_id, start=start, end=end
        )
        return _completed_views(sessions)
