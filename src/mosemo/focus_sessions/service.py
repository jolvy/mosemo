from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.models import ActivityRecord
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.service import (
    ActivityAccountNotFoundError,
    ActivityDeviceNotFoundError,
    timeline_date_window,
)
from mosemo.activity_labels.catalog.models import Label
from mosemo.devices.repository import DeviceRepository
from mosemo.focus_sessions.models import (
    FocusSessionConflictError,
    FocusSessionNotFoundError,
)
from mosemo.focus_sessions.repository import FocusSessionRepository
from mosemo.focus_sessions.schemas import (
    FocusSessionCompleteRequest,
    FocusSessionCreateRequest,
    FocusSessionResponse,
)


class FocusSessionLabelNotAvailableError(Exception):
    pass


class FocusSessionService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: FocusSessionRepository,
        activity_repository: ActivityRepository,
        device_repository: DeviceRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._activity_repository = activity_repository
        self._device_repository = device_repository

    async def create(
        self, account_id: UUID, request: FocusSessionCreateRequest
    ) -> FocusSessionResponse:
        async with self._session.begin():
            device = await self._device_repository.find_owned_by_id(
                account_id=account_id, device_id=request.device_id
            )
            if device is None:
                raise ActivityDeviceNotFoundError
            stored = await self._repository.create(account_id, request)
            if stored is None:
                stored = await self._repository.find_owned(
                    account_id, request.session_id
                )
                if stored is None:
                    raise FocusSessionConflictError
                if (stored.device_id, stored.started_at, stored.target_seconds) != (
                    request.device_id,
                    request.started_at,
                    request.target_seconds,
                ):
                    raise FocusSessionConflictError
            return FocusSessionResponse.model_validate(stored, from_attributes=True)

    async def complete(
        self, account_id: UUID, session_id: UUID, request: FocusSessionCompleteRequest
    ) -> FocusSessionResponse:
        async with self._session.begin():
            stored = await self._repository.find_owned(account_id, session_id)
            if stored is None:
                raise FocusSessionNotFoundError
            if stored.ended_at is None:
                label = await self._session.scalar(
                    select(Label).where(
                        Label.label_id == request.label_id,
                        Label.account_id == account_id,
                        Label.archived_at.is_(None),
                    )
                )
                if label is None:
                    raise FocusSessionLabelNotAvailableError
            last_observed_at = await self._session.scalar(
                select(func.max(ActivityRecord.observed_at)).where(
                    ActivityRecord.focus_session_id == session_id
                )
            )
            stored.complete(**request.model_dump(), last_observed_at=last_observed_at)
            await self._session.flush()
            return FocusSessionResponse.model_validate(stored, from_attributes=True)

    async def list_completed(
        self, account_id: UUID, day: date
    ) -> list[FocusSessionResponse]:
        async with self._session.begin():
            timezone = await self._activity_repository.find_account_timezone(
                account_id
            )
            if timezone is None:
                raise ActivityAccountNotFoundError
            window = timeline_date_window(
                account_timezone=timezone,
                requested_date=day,
                current_time=datetime.now(UTC),
            )
            if window is None:
                return []
            _, start, end = window
            sessions = await self._repository.list_completed(account_id, start, end)
            return [
                FocusSessionResponse.model_validate(item, from_attributes=True)
                for item in sessions
            ]
