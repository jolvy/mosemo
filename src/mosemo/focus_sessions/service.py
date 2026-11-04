from datetime import date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.repository import ActivityRepository
from mosemo.activities.service import ActivityDeviceNotFoundError
from mosemo.focus_sessions.mappers import FocusSessionMapper
from mosemo.focus_sessions.models import (
    FocusSession,
    FocusSessionConflictError,
    FocusSessionNotFoundError,
)
from mosemo.focus_sessions.repository import FocusSessionRepository
from mosemo.focus_sessions.schemas import (
    FocusSessionCompleteRequest,
    FocusSessionCreateRequest,
    FocusSessionResponse,
)
from mosemo.identity.public import AccountDirectory, DeviceDirectory
from mosemo.labels.public import LabelCatalog
from mosemo.shared.application.clock import Clock
from mosemo.shared.domain.timezones import timeline_date_window


class FocusSessionLabelNotAvailableError(Exception):
    pass


class FocusSessionCommands:
    def __init__(
        self,
        *,
        repository: FocusSessionRepository,
        devices: DeviceDirectory,
        labels: LabelCatalog,
        activity_repository: ActivityRepository,
    ) -> None:
        self._repository = repository
        self._devices = devices
        self._labels = labels
        self._activity_repository = activity_repository
        self._mapper = FocusSessionMapper()

    async def create(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        request: FocusSessionCreateRequest,
    ) -> FocusSession:
        owned = await self._devices.find_owned(
            db_session, account_id=account_id, device_id=request.device_id
        )
        if owned is None:
            raise ActivityDeviceNotFoundError
        candidate = self._mapper.to_entity(request, account_id=account_id)
        stored = await self._repository.add_if_absent(candidate)
        if stored is None:
            stored = await self._repository.find_owned(account_id, request.session_id)
            if stored is None or not stored.has_same_start(candidate):
                raise FocusSessionConflictError
        return stored

    async def complete(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        session_id: UUID,
        request: FocusSessionCompleteRequest,
    ) -> FocusSession:
        stored = await self._repository.find_owned(account_id, session_id)
        if stored is None:
            raise FocusSessionNotFoundError
        if stored.ended_at is None and not await self._labels.is_active(
            db_session, account_id=account_id, label_id=request.label_id
        ):
            raise FocusSessionLabelNotAvailableError
        last_observed_at = await self._activity_repository.find_last_observed_at(
            session_id
        )
        stored.complete(**request.model_dump(), last_observed_at=last_observed_at)
        return stored


class FocusSessionQueries:
    def __init__(
        self,
        *,
        repository: FocusSessionRepository,
        accounts: AccountDirectory,
    ) -> None:
        self._repository = repository
        self._accounts = accounts

    async def list_completed(
        self, db_session: AsyncSession, *, account_id: UUID, day: date, now: datetime
    ) -> list[FocusSession]:
        timezone = await self._accounts.get_timezone(db_session, account_id)
        window = timeline_date_window(
            account_timezone=timezone, requested_date=day, now=now
        )
        if window is None:
            return []
        _, start, end = window
        return await self._repository.list_completed(account_id, start, end=end)


class FocusSessionService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        commands: FocusSessionCommands,
        queries: FocusSessionQueries,
        clock: Clock,
    ) -> None:
        self._session = session
        self._commands = commands
        self._queries = queries
        self._clock = clock

    async def create(
        self, account_id: UUID, request: FocusSessionCreateRequest
    ) -> FocusSessionResponse:
        async with self._session.begin():
            stored = await self._commands.create(
                self._session, account_id=account_id, request=request
            )
            return FocusSessionResponse.model_validate(stored, from_attributes=True)

    async def complete(
        self,
        account_id: UUID,
        session_id: UUID,
        *,
        request: FocusSessionCompleteRequest,
    ) -> FocusSessionResponse:
        async with self._session.begin():
            stored = await self._commands.complete(
                self._session,
                account_id=account_id,
                session_id=session_id,
                request=request,
            )
            await self._session.flush()
            return FocusSessionResponse.model_validate(stored, from_attributes=True)

    async def list_completed(
        self, account_id: UUID, day: date
    ) -> list[FocusSessionResponse]:
        async with self._session.begin():
            sessions = await self._queries.list_completed(
                self._session, account_id=account_id, day=day, now=self._clock()
            )
            return [
                FocusSessionResponse.model_validate(item, from_attributes=True)
                for item in sessions
            ]
