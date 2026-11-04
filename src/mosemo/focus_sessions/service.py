from datetime import date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.repository import ActivityRepository
from mosemo.activities.service import (
    ActivityAccountNotFoundError,
    ActivityDeviceNotFoundError,
    timeline_date_window,
)
from mosemo.activity_labels.catalog.repository import LabelCatalogRepository
from mosemo.devices.repository import DeviceRepository
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
from mosemo.shared.application.clock import Clock


class FocusSessionLabelNotAvailableError(Exception):
    pass


class FocusSessionCommands:
    def __init__(
        self,
        *,
        repository: FocusSessionRepository,
        device_repository: DeviceRepository,
        label_catalog_repository: LabelCatalogRepository,
        activity_repository: ActivityRepository,
    ) -> None:
        self._repository = repository
        self._device_repository = device_repository
        self._label_catalog_repository = label_catalog_repository
        self._activity_repository = activity_repository
        self._mapper = FocusSessionMapper()

    async def create(
        self, *, account_id: UUID, request: FocusSessionCreateRequest
    ) -> FocusSession:
        device = await self._device_repository.find_owned_by_id(
            account_id=account_id, device_id=request.device_id
        )
        if device is None:
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
        *,
        account_id: UUID,
        session_id: UUID,
        request: FocusSessionCompleteRequest,
    ) -> FocusSession:
        stored = await self._repository.find_owned(account_id, session_id)
        if stored is None:
            raise FocusSessionNotFoundError
        if stored.ended_at is None:
            label = await self._label_catalog_repository.find_active_owned(
                account_id=account_id, label_id=request.label_id
            )
            if label is None:
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
        activity_repository: ActivityRepository,
    ) -> None:
        self._repository = repository
        self._activity_repository = activity_repository

    async def list_completed(
        self, *, account_id: UUID, day: date, now: datetime
    ) -> list[FocusSession]:
        timezone = await self._activity_repository.find_account_timezone(account_id)
        if timezone is None:
            raise ActivityAccountNotFoundError
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
            stored = await self._commands.create(account_id=account_id, request=request)
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
                account_id=account_id, session_id=session_id, request=request
            )
            await self._session.flush()
            return FocusSessionResponse.model_validate(stored, from_attributes=True)

    async def list_completed(
        self, account_id: UUID, day: date
    ) -> list[FocusSessionResponse]:
        async with self._session.begin():
            sessions = await self._queries.list_completed(
                account_id=account_id, day=day, now=self._clock()
            )
            return [
                FocusSessionResponse.model_validate(item, from_attributes=True)
                for item in sessions
            ]
