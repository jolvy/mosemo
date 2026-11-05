from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.labeling.application.states import (
    SegmentLabelState,
    find_labelable_segment,
    proposal_state,
)
from mosemo.labeling.domain.confirmations import (
    ActivityLabelConfirmation,
    ActivityLabelConfirmationConflictError,
    ActivityLabelSegmentChangedError,
    ActivityLabelSegmentDuplicateError,
    ActivityLabelSegmentNotFoundError,
    ActivityLabelSegmentNotLabelableError,
    ActivityLabelUnavailableError,
)
from mosemo.labeling.infrastructure.confirmations import ConfirmationRepository
from mosemo.labeling.infrastructure.proposals import ProposalRepository
from mosemo.labels.public import LabelCatalog
from mosemo.observation.public import ObservationTimeline, ObservedSegment
from mosemo.platform.database.transactions import unit_of_work
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.application.use_case import UseCase

type SelectionField = Literal["segment_id", "segment_version", "selection"]


@dataclass(frozen=True, slots=True)
class LabelSelection:
    """The choice for one segment version; `label_id` `None` is unclassified."""

    segment_id: UUID
    segment_version: str
    label_id: UUID | None


class BatchLabelConfirmationError(Exception):
    """Item `item_index` of a batch failed with `reason` at `field_name`."""

    def __init__(
        self,
        *,
        item_index: int,
        reason: Exception,
        field_name: SelectionField,
    ) -> None:
        super().__init__(str(reason))
        self.item_index = item_index
        self.field_name: SelectionField = field_name
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _ConfirmationTarget:
    segment: ObservedSegment
    version: str
    label_id: UUID | None


class SegmentConfirmations:
    """Reads and saves confirmations inside the calling use case's transaction."""

    def __init__(
        self,
        *,
        confirmations: ConfirmationRepository,
        proposals: ProposalRepository,
    ) -> None:
        self._confirmations = confirmations
        self._proposals = proposals

    async def find(
        self, db_session: AsyncSession, *, account_id: UUID, first_event_id: UUID
    ) -> ActivityLabelConfirmation | None:
        return await self._confirmations.find(
            db_session, account_id=account_id, first_event_id=first_event_id
        )

    async def save(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        target: _ConfirmationTarget,
        now: datetime,
    ) -> SegmentLabelState:
        segment = target.segment
        confirmation = await self._confirmations.find(
            db_session, account_id=account_id, first_event_id=segment.first_event_id
        )
        if confirmation is None:
            confirmation = ActivityLabelConfirmation(
                account_id=account_id,
                first_event_id=segment.first_event_id,
                segment_version=target.version,
                label_id=target.label_id,
                confirmed_at=now,
                updated_at=now,
            )
            self._confirmations.add(db_session, confirmation)
        else:
            confirmation.apply_selection(
                segment_version=target.version,
                label_id=target.label_id,
                now=now,
            )
        await db_session.flush()
        proposal = await self._proposals.find(
            db_session,
            account_id=account_id,
            first_event_id=segment.first_event_id,
            segment_version=target.version,
        )
        return SegmentLabelState(
            segment_id=segment.id,
            segment_version=target.version,
            confirmation=confirmation,
            proposal=proposal_state(proposal, now=now),
        )


class ConfirmSegmentLabel(UseCase):
    def __init__(
        self,
        *,
        confirmations: SegmentConfirmations,
        labels: LabelCatalog,
        timeline: ObservationTimeline,
        dispatcher: EventDispatcher,
        clock: Clock,
    ) -> None:
        self._confirmations = confirmations
        self._labels = labels
        self._timeline = timeline
        self._dispatcher = dispatcher
        self._clock = clock

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, selection: LabelSelection
    ) -> SegmentLabelState:
        """Confirm or correct the segment's choice under the account timeline lock.

        Raises `ActivityLabelSegmentNotFoundError`,
        `ActivityLabelSegmentNotLabelableError`, `ActivityLabelSegmentChangedError`,
        `ActivityLabelUnavailableError` and observation's `ActivityTimelineBusyError`.
        """
        now = self._clock()
        async with unit_of_work(db_session, self._dispatcher):
            await self._timeline.lock(db_session, account_id)
            segment = await find_labelable_segment(
                self._timeline,
                db_session,
                account_id=account_id,
                segment_id=selection.segment_id,
                now=now,
            )
            version = segment.version
            assert version is not None
            if selection.segment_version != version:
                raise ActivityLabelSegmentChangedError
            label_id = selection.label_id
            if label_id is not None and not await self._labels.is_active(
                db_session, account_id=account_id, label_id=label_id
            ):
                raise ActivityLabelUnavailableError
            return await self._confirmations.save(
                db_session,
                account_id=account_id,
                target=_ConfirmationTarget(
                    segment=segment, version=version, label_id=label_id
                ),
                now=now,
            )


class ConfirmSegmentLabels(UseCase):
    def __init__(
        self,
        *,
        confirmations: SegmentConfirmations,
        labels: LabelCatalog,
        timeline: ObservationTimeline,
        dispatcher: EventDispatcher,
        clock: Clock,
    ) -> None:
        self._confirmations = confirmations
        self._labels = labels
        self._timeline = timeline
        self._dispatcher = dispatcher
        self._clock = clock

    async def execute(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        selections: list[LabelSelection],
    ) -> list[SegmentLabelState]:
        """Apply every selection or none, returning states in request order.

        Raises `BatchLabelConfirmationError` for the first invalid item and
        observation's `ActivityTimelineBusyError`.
        """
        _reject_duplicate_segments(selections)
        now = self._clock()
        async with unit_of_work(db_session, self._dispatcher):
            await self._timeline.lock(db_session, account_id)
            targets = [
                await self._validate(
                    db_session,
                    account_id=account_id,
                    selection=selection,
                    item_index=item_index,
                    now=now,
                )
                for item_index, selection in enumerate(selections)
            ]
            return [
                await self._confirmations.save(
                    db_session, account_id=account_id, target=target, now=now
                )
                for target in targets
            ]

    async def _validate(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        selection: LabelSelection,
        item_index: int,
        now: datetime,
    ) -> _ConfirmationTarget:
        try:
            segment = await find_labelable_segment(
                self._timeline,
                db_session,
                account_id=account_id,
                segment_id=selection.segment_id,
                now=now,
            )
        except (
            ActivityLabelSegmentNotFoundError,
            ActivityLabelSegmentNotLabelableError,
        ) as exc:
            raise BatchLabelConfirmationError(
                item_index=item_index, reason=exc, field_name="segment_id"
            ) from exc
        version = segment.version
        assert version is not None
        if selection.segment_version != version:
            raise BatchLabelConfirmationError(
                item_index=item_index,
                reason=ActivityLabelSegmentChangedError(),
                field_name="segment_version",
            )
        label_id = selection.label_id
        confirmation = await self._confirmations.find(
            db_session, account_id=account_id, first_event_id=segment.first_event_id
        )
        is_idempotent = _validate_selection(
            item_index=item_index,
            label_id=label_id,
            segment_version=version,
            confirmation=confirmation,
        )
        if (
            label_id is not None
            and not is_idempotent
            and not await self._labels.is_active(
                db_session, account_id=account_id, label_id=label_id
            )
        ):
            raise BatchLabelConfirmationError(
                item_index=item_index,
                reason=ActivityLabelUnavailableError(),
                field_name="selection",
            )
        return _ConfirmationTarget(segment=segment, version=version, label_id=label_id)


def _reject_duplicate_segments(selections: list[LabelSelection]) -> None:
    seen: set[UUID] = set()
    for item_index, selection in enumerate(selections):
        if selection.segment_id in seen:
            raise BatchLabelConfirmationError(
                item_index=item_index,
                reason=ActivityLabelSegmentDuplicateError(),
                field_name="segment_id",
            )
        seen.add(selection.segment_id)


def _validate_selection(
    *,
    item_index: int,
    label_id: UUID | None,
    segment_version: str,
    confirmation: ActivityLabelConfirmation | None,
) -> bool:
    """Whether the selection repeats the stored one; raise on a conflict."""
    if confirmation is None:
        return False
    if not confirmation.accepts_batch_selection(
        segment_version=segment_version, label_id=label_id
    ):
        raise BatchLabelConfirmationError(
            item_index=item_index,
            reason=ActivityLabelConfirmationConflictError(),
            field_name="selection",
        )
    return confirmation.matches_batch_selection(
        segment_version=segment_version, label_id=label_id
    )
