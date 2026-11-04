from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.public import AccountDirectory, DeviceDirectory
from mosemo.infra.transactions import read_snapshot, unit_of_work
from mosemo.labels.public import LabelCatalog
from mosemo.observation.domain.activities import (
    ActivityDeviceNotFoundError,
    ActivityRecord,
)
from mosemo.observation.domain.focus_sessions import (
    FocusSession,
    FocusSessionConflictError,
    FocusSessionNotFoundError,
)
from mosemo.observation.infrastructure.activity_records import (
    ActivityRecordRepository,
)
from mosemo.observation.infrastructure.focus_sessions import FocusSessionRepository
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.application.use_case import UseCase
from mosemo.shared.domain.timezones import timeline_date_window


class FocusSessionLabelNotAvailableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FocusSessionCompletion:
    ended_at: datetime
    work_seconds: int
    label_id: UUID
    description: str


class FocusSessionLinks:
    """Checks the focus session that an activity record links to.

    Runs inside the caller's transaction and locks the session `FOR UPDATE`.
    """

    def __init__(self, *, focus_sessions: FocusSessionRepository) -> None:
        self._focus_sessions = focus_sessions

    async def validate(
        self, db_session: AsyncSession, *, account_id: UUID, record: ActivityRecord
    ) -> None:
        """Raise `FocusSessionNotFoundError` or `FocusSessionInvalidTimeError`."""
        if record.focus_session_id is None:
            return
        focus = await self._focus_sessions.find_owned(
            db_session, account_id=account_id, focus_session_id=record.focus_session_id
        )
        if focus is None:
            raise FocusSessionNotFoundError
        focus.validate_activity(
            account_id=account_id,
            device_id=record.device_id,
            observed_at=record.observed_at,
        )


class StartFocusSession(UseCase):
    def __init__(
        self,
        *,
        focus_sessions: FocusSessionRepository,
        devices: DeviceDirectory,
        dispatcher: EventDispatcher,
    ) -> None:
        self._focus_sessions = focus_sessions
        self._devices = devices
        self._dispatcher = dispatcher

    async def execute(
        self, db_session: AsyncSession, focus_session: FocusSession
    ) -> FocusSession:
        """Start the session once; a retry with the same start returns it.

        Raises `ActivityDeviceNotFoundError` for a device the account does not
        own and `FocusSessionConflictError` for the same ID with another start.
        """
        async with unit_of_work(db_session, self._dispatcher):
            owned = await self._devices.find_owned(
                db_session,
                account_id=focus_session.account_id,
                device_id=focus_session.device_id,
            )
            if owned is None:
                raise ActivityDeviceNotFoundError
            stored, created = await self._focus_sessions.get_or_create(
                db_session, focus_session
            )
            if not created and (
                stored.account_id != focus_session.account_id
                or not stored.has_same_start(focus_session)
            ):
                raise FocusSessionConflictError
        return stored


class CompleteFocusSession(UseCase):
    def __init__(
        self,
        *,
        focus_sessions: FocusSessionRepository,
        activity_records: ActivityRecordRepository,
        labels: LabelCatalog,
        dispatcher: EventDispatcher,
    ) -> None:
        self._focus_sessions = focus_sessions
        self._activity_records = activity_records
        self._labels = labels
        self._dispatcher = dispatcher

    async def execute(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        focus_session_id: UUID,
        completion: FocusSessionCompletion,
    ) -> FocusSession:
        """Record the completion once; an identical retry returns the session.

        Raises `FocusSessionNotFoundError`, `FocusSessionLabelNotAvailableError`,
        and the session's `FocusSessionConflictError`/`FocusSessionInvalidTimeError`.
        """
        async with unit_of_work(db_session, self._dispatcher):
            stored = await self._focus_sessions.find_owned(
                db_session, account_id=account_id, focus_session_id=focus_session_id
            )
            if stored is None:
                raise FocusSessionNotFoundError
            if stored.ended_at is None and not await self._labels.is_active(
                db_session, account_id=account_id, label_id=completion.label_id
            ):
                raise FocusSessionLabelNotAvailableError
            last_observed_at = await self._activity_records.find_last_observed_at(
                db_session, focus_session_id
            )
            stored.complete(
                ended_at=completion.ended_at,
                work_seconds=completion.work_seconds,
                label_id=completion.label_id,
                description=completion.description,
                last_observed_at=last_observed_at,
            )
        return stored


class ListCompletedFocusSessions(UseCase):
    def __init__(
        self,
        *,
        focus_sessions: FocusSessionRepository,
        accounts: AccountDirectory,
        clock: Clock,
    ) -> None:
        self._focus_sessions = focus_sessions
        self._accounts = accounts
        self._clock = clock

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, day: date
    ) -> list[FocusSession]:
        """Completed sessions that started on the account's calendar day, newest first.

        Raises identity's `AccountNotFoundError` when the account does not exist.
        """
        now = self._clock()
        async with read_snapshot(db_session):
            timezone = await self._accounts.get_timezone(db_session, account_id)
            window = timeline_date_window(
                account_timezone=timezone, requested_date=day, now=now
            )
            if window is None:
                return []
            _, start, end = window
            return await self._focus_sessions.list_completed(
                db_session, account_id, start=start, end=end
            )
