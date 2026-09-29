from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.repository import ActivityRepository
from mosemo.activities.service import acquire_activity_timeline_lock
from mosemo.activity_labels.catalog.repository import LabelCatalogRepository
from mosemo.activity_labels.confirmations.repository import ConfirmationRepository
from mosemo.activity_labels.confirmations.service import ConfirmationService
from mosemo.activity_labels.proposals.repository import ProposalRepository
from mosemo.activity_labels.queries.repository import LabelQueryRepository
from mosemo.activity_labels.queries.service import LabelQueries
from mosemo.activity_labels.schemas import (
    ActivityLabelConfirmationRequest,
    ActivityLabelStateResponse,
    BatchLabelConfirmationRequest,
    BatchLabelConfirmationResponse,
    ConfirmedActivityLabelStateResponse,
    LabelTimelineItemResponse,
)


class ActivityLabelService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        activity_repository: ActivityRepository,
        confirmation_repository: ConfirmationRepository,
        label_catalog_repository: LabelCatalogRepository,
        proposal_repository: ProposalRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._session = session
        self._confirmations = ConfirmationService(
            session=session,
            activity_repository=activity_repository,
            confirmation_repository=confirmation_repository,
            label_catalog_repository=label_catalog_repository,
        )
        self._queries = LabelQueries(
            activity_repository=activity_repository,
            confirmation_repository=confirmation_repository,
            proposal_repository=proposal_repository,
            label_query_repository=LabelQueryRepository(session),
        )
        self._clock = clock

    async def get_timeline(
        self, *, account_id: UUID, date: date | None = None, now: datetime | None = None
    ) -> list[LabelTimelineItemResponse]:
        current_time = now or self._clock()
        async with self._session.begin():
            return await self._queries.get_timeline(
                account_id=account_id, date=date, current_time=current_time
            )

    async def get_state(
        self, *, account_id: UUID, segment_id: UUID, now: datetime | None = None
    ) -> ActivityLabelStateResponse:
        current_time = now or datetime.now(UTC)
        async with self._session.begin():
            return await self._queries.get_state(
                account_id=account_id, segment_id=segment_id, current_time=current_time
            )

    async def confirm(
        self,
        *,
        account_id: UUID,
        segment_id: UUID,
        request: ActivityLabelConfirmationRequest,
        now: datetime | None = None,
    ) -> ConfirmedActivityLabelStateResponse:
        current_time = now or datetime.now(UTC)
        async with self._session.begin():
            await acquire_activity_timeline_lock(self._session, account_id=account_id)
            saved = await self._confirmations.confirm(
                account_id=account_id,
                segment_id=segment_id,
                request=request,
                current_time=current_time,
            )
            return await self._queries.confirmed_state(
                account_id=account_id, saved=saved, current_time=current_time
            )

    async def confirm_batch(
        self,
        *,
        account_id: UUID,
        request: BatchLabelConfirmationRequest,
        now: datetime | None = None,
    ) -> BatchLabelConfirmationResponse:
        self._confirmations.reject_duplicate_segments(request)
        current_time = now or datetime.now(UTC)
        async with self._session.begin():
            await acquire_activity_timeline_lock(self._session, account_id=account_id)
            saved = await self._confirmations.confirm_batch(
                account_id=account_id, request=request, current_time=current_time
            )
            return BatchLabelConfirmationResponse(
                items=[
                    await self._queries.confirmed_state(
                        account_id=account_id, saved=item, current_time=current_time
                    )
                    for item in saved
                ]
            )
