from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activity_labels.confirmations.service import ConfirmationService
from mosemo.activity_labels.queries.service import LabelQueries
from mosemo.activity_labels.schemas import (
    ActivityLabelConfirmationRequest,
    ActivityLabelStateResponse,
    BatchLabelConfirmationRequest,
    BatchLabelConfirmationResponse,
    ConfirmedActivityLabelStateResponse,
    LabelTimelineItemResponse,
)
from mosemo.observation.public import ObservationTimeline
from mosemo.shared.application.clock import Clock


class ActivityLabelService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        timeline: ObservationTimeline,
        confirmations: ConfirmationService,
        queries: LabelQueries,
        clock: Clock,
    ) -> None:
        self._session = session
        self._timeline = timeline
        self._confirmations = confirmations
        self._queries = queries
        self._clock = clock

    async def get_timeline(
        self, *, account_id: UUID, date: date | None = None
    ) -> list[LabelTimelineItemResponse]:
        now = self._clock()
        async with self._session.begin():
            return await self._queries.get_timeline(
                self._session, account_id=account_id, date=date, now=now
            )

    async def get_state(
        self, *, account_id: UUID, segment_id: UUID
    ) -> ActivityLabelStateResponse:
        now = self._clock()
        async with self._session.begin():
            return await self._queries.get_state(
                self._session, account_id=account_id, segment_id=segment_id, now=now
            )

    async def confirm(
        self,
        *,
        account_id: UUID,
        segment_id: UUID,
        request: ActivityLabelConfirmationRequest,
    ) -> ConfirmedActivityLabelStateResponse:
        now = self._clock()
        async with self._session.begin():
            await self._timeline.lock(self._session, account_id)
            saved = await self._confirmations.confirm(
                account_id=account_id,
                segment_id=segment_id,
                request=request,
                now=now,
            )
            return await self._queries.confirmed_state(
                account_id=account_id, saved=saved, now=now
            )

    async def confirm_batch(
        self,
        *,
        account_id: UUID,
        request: BatchLabelConfirmationRequest,
    ) -> BatchLabelConfirmationResponse:
        self._confirmations.reject_duplicate_segments(request)
        now = self._clock()
        async with self._session.begin():
            await self._timeline.lock(self._session, account_id)
            saved = await self._confirmations.confirm_batch(
                account_id=account_id, request=request, now=now
            )
            return BatchLabelConfirmationResponse(
                items=[
                    await self._queries.confirmed_state(
                        account_id=account_id, saved=item, now=now
                    )
                    for item in saved
                ]
            )
