"""Activity batch processing inside the transaction its use case opens."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.public import DeviceDirectory
from mosemo.infra.transactions import read_snapshot, unit_of_work
from mosemo.observation.application.focus_sessions import FocusSessionLinks
from mosemo.observation.application.ports import (
    ActivityBatchPublisher,
    ActivityDeliveryUnavailableError,
)
from mosemo.observation.domain.activities import (
    ActivityDeviceNotFoundError,
    ActivityRecord,
)
from mosemo.observation.domain.focus_sessions import (
    FocusSessionInvalidTimeError,
    FocusSessionNotFoundError,
)
from mosemo.observation.domain.timeline import (
    ActivityTimelineSegment,
    ProjectedActivity,
    ProjectedGap,
    ProjectedSegment,
    ProjectionError,
    SegmentType,
    TimelineProjection,
    observation_key,
)
from mosemo.observation.infrastructure.activity_records import (
    ActivityRecordRepository,
)
from mosemo.observation.infrastructure.batch_receipts import BatchReceiptRepository
from mosemo.observation.infrastructure.focus_sessions import FocusSessionRepository
from mosemo.observation.infrastructure.timeline_segments import (
    TimelineSegmentRepository,
)
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.application.use_case import UseCase


class TerminalBatchError(Exception):
    """Safe diagnostic code only; never include source text or tracebacks."""


@dataclass(frozen=True, slots=True)
class ActivityBatch:
    """One device's records to process, with the fingerprint of their content."""

    batch_id: UUID
    device_id: UUID
    fingerprint: str
    records: tuple[ActivityRecord, ...]


def segment_seed(segment: ActivityTimelineSegment) -> ProjectedSegment:
    if segment.segment_type is SegmentType.ACTIVITY:
        assert segment.last_event_id is not None
        assert segment.last_observed_at is not None
        assert segment.context is not None
        # Silence closure is derived; the next observation can extend it.
        return ProjectedActivity(
            segment.started_at,
            segment.first_event_id,
            segment.last_event_id,
            segment.last_observed_at,
            segment.context,
        )
    assert segment.reason is not None
    return ProjectedGap(segment.started_at, segment.first_event_id, segment.reason)


@dataclass(frozen=True, slots=True)
class _AccountScope:
    """The account being projected, its devices, and the processing time."""

    account_id: UUID
    device_ids: Sequence[UUID]
    now: datetime


class ActivityBatchProcessor:
    """Stores a batch, its receipt and its projection.

    Stateless; runs inside the transaction its use case opened and takes that
    use case's `db_session` as the first argument.
    """

    def __init__(self, *, clock: Clock, devices: DeviceDirectory) -> None:
        self._clock = clock
        self._devices = devices
        self._records = ActivityRecordRepository()
        self._segments = TimelineSegmentRepository()
        self._receipts = BatchReceiptRepository()
        self._focus_sessions = FocusSessionLinks(
            focus_sessions=FocusSessionRepository()
        )

    async def process(
        self, db_session: AsyncSession, *, account_id: UUID, batch: ActivityBatch
    ) -> list[dict[str, str]]:
        now = self._clock()
        await self._ensure_device_owned(
            db_session, account_id=account_id, device_id=batch.device_id
        )
        initialized = await self._receipts.has_for_account(db_session, account_id)
        claimed = await self._receipts.claim(
            db_session,
            account_id=account_id,
            device_id=batch.device_id,
            batch_id=batch.batch_id,
            fingerprint=batch.fingerprint,
        )
        if claimed is None:
            return await self._winner_results(
                db_session, account_id=account_id, batch=batch
            )
        scope = _AccountScope(
            account_id=account_id,
            device_ids=await self._devices.list_device_ids(db_session, account_id),
            now=now,
        )
        latest = await self._initialize_account(
            db_session, scope, initialized=initialized
        )
        adopted, results = await self._store_records(
            db_session, account_id=account_id, batch=batch
        )
        await self._ensure_device_order(db_session, adopted)
        if adopted:
            try:
                await self._project(db_session, scope, adopted=adopted, latest=latest)
            except ProjectionError:
                raise TerminalBatchError("projection_time_regression") from None
        claimed.results = results
        await db_session.flush()
        return results

    async def _ensure_device_owned(
        self, db_session: AsyncSession, *, account_id: UUID, device_id: UUID
    ) -> None:
        owned = await self._devices.find_owned(
            db_session, account_id=account_id, device_id=device_id
        )
        if owned is None:
            raise TerminalBatchError("device_not_owned")

    async def _winner_results(
        self, db_session: AsyncSession, *, account_id: UUID, batch: ActivityBatch
    ) -> list[dict[str, str]]:
        winner = await self._receipts.find(
            db_session,
            account_id=account_id,
            device_id=batch.device_id,
            batch_id=batch.batch_id,
        )
        if winner is None:
            raise RuntimeError("receipt winner missing")
        if winner.fingerprint != batch.fingerprint:
            raise TerminalBatchError("batch_content_conflict")
        return winner.results

    async def _initialize_account(
        self, db_session: AsyncSession, scope: _AccountScope, *, initialized: bool
    ) -> ActivityRecord | None:
        if not initialized and not await self._records.is_account_clock_monotone(
            db_session, scope.device_ids
        ):
            raise TerminalBatchError("projection_time_regression")
        latest = await self._records.find_latest_account_record(
            db_session, scope.device_ids
        )
        if not initialized and latest is not None:
            await self._rebuild_legacy_projection(db_session, scope, latest=latest)
        return latest

    async def _store_records(
        self, db_session: AsyncSession, *, account_id: UUID, batch: ActivityBatch
    ) -> tuple[list[ActivityRecord], list[dict[str, str]]]:
        adopted = []
        results = []
        for record in batch.records:
            stored, status = await self._store_record(
                db_session, account_id=account_id, record=record
            )
            if stored is not None:
                adopted.append(stored)
            results.append({"eventId": str(record.event_id), "status": status})
        return adopted, results

    async def _store_record(
        self, db_session: AsyncSession, *, account_id: UUID, record: ActivityRecord
    ) -> tuple[ActivityRecord | None, str]:
        # Processed IDs are immutable. Pre-reads do not replace INSERT's
        # unique constraints: a concurrent winner comes back from get_or_create.
        status = await self._rejection_status(db_session, record)
        if status is not None:
            return None, status
        await self._validate_focus_session(
            db_session, account_id=account_id, record=record
        )
        stored, created = await self._records.get_or_create(db_session, record)
        if created:
            return stored, "stored"
        return None, _conflict_status(record, stored)

    async def _rejection_status(
        self, db_session: AsyncSession, record: ActivityRecord
    ) -> str | None:
        event = await self._records.find_by_event_id(db_session, record.event_id)
        if event is None:
            event = await self._records.find_by_device_sequence(
                db_session, device_id=record.device_id, sequence=record.sequence
            )
        return None if event is None else _conflict_status(record, event)

    async def _validate_focus_session(
        self, db_session: AsyncSession, *, account_id: UUID, record: ActivityRecord
    ) -> None:
        try:
            await self._focus_sessions.validate(
                db_session, account_id=account_id, record=record
            )
        except (
            FocusSessionNotFoundError,
            FocusSessionInvalidTimeError,
        ) as error:
            raise TerminalBatchError(type(error).__name__) from None

    async def _ensure_device_order(
        self, db_session: AsyncSession, adopted: list[ActivityRecord]
    ) -> None:
        for record in adopted:
            previous = await self._records.find_previous_device_observed_at(
                db_session, record
            )
            following = await self._records.find_next_device_observed_at(
                db_session, record
            )
            if not record.is_in_device_order(previous=previous, following=following):
                raise TerminalBatchError("projection_time_regression")

    async def _rebuild_legacy_projection(
        self, db_session: AsyncSession, scope: _AccountScope, *, latest: ActivityRecord
    ) -> None:
        # The first receipt marks an atomic transition from received_at tie order.
        # Rebuild even for duplicate-only batches; an unchanged prefix does not
        # prove that later legacy ties already follow the new ordering.
        start = datetime.min.replace(tzinfo=UTC)
        projection = TimelineProjection()
        after = None
        while page := await self._records.list_record_page(
            db_session, scope.device_ids, start=start, after=after
        ):
            for record in page:
                projection.feed(record)
            after = observation_key(page[-1])
        existing = await self._segments.list_segment_range(
            db_session, scope.account_id, start=start
        )
        await self._segments.replace_account_segments(
            db_session,
            account_id=scope.account_id,
            projected=projection.finish(scope.now),
            existing=existing,
            # Keep replacement from converging before every legacy tie is rebuilt.
            event_observed_at=latest.observed_at,
        )

    async def _project(
        self,
        db_session: AsyncSession,
        scope: _AccountScope,
        *,
        adopted: list[ActivityRecord],
        latest: ActivityRecord | None,
    ) -> None:
        ordered = sorted(adopted, key=observation_key)
        if latest is None or observation_key(ordered[0]) > observation_key(latest):
            last_segment = await self._segments.find_last_account_segment(
                db_session, scope.account_id
            )
            if last_segment is not None:
                await self._append(
                    db_session, scope, ordered=ordered, last_segment=last_segment
                )
                return
        await self._replay(db_session, scope, ordered=ordered)

    async def _append(
        self,
        db_session: AsyncSession,
        scope: _AccountScope,
        *,
        ordered: list[ActivityRecord],
        last_segment: ActivityTimelineSegment,
    ) -> None:
        projection = TimelineProjection(segment_seed(last_segment))
        for record in ordered:
            projection.feed(record)
        await self._segments.replace_account_segments(
            db_session,
            account_id=scope.account_id,
            projected=projection.finish(scope.now),
            existing=[last_segment],
            event_observed_at=ordered[-1].observed_at,
        )

    async def _replay(
        self,
        db_session: AsyncSession,
        scope: _AccountScope,
        *,
        ordered: list[ActivityRecord],
    ) -> None:
        observed_at = ordered[-1].observed_at
        start = await self._segments.find_replay_start(
            db_session, scope.account_id, observed_at=ordered[0].observed_at
        )
        projection = TimelineProjection()
        after = None
        remaining = {r.event_id for r in ordered}
        convergence = None
        while convergence is None and (
            page := await self._records.list_record_page(
                db_session, scope.device_ids, start=start, after=after
            )
        ):
            for record in page:
                remaining.discard(record.event_id)
                closed = projection.feed(record)
                if closed is not None and not remaining:
                    convergence = await self._converged_segment(
                        db_session,
                        account_id=scope.account_id,
                        closed=closed,
                        observed_at=observed_at,
                    )
                    if convergence is not None:
                        break
            after = observation_key(page[-1])
        existing = await self._segments.list_segment_range(
            db_session,
            scope.account_id,
            start=start,
            through=convergence.started_at if convergence else None,
        )
        projected = (
            projection.segments[:-1] if convergence else projection.finish(scope.now)
        )
        await self._segments.replace_account_segments(
            db_session,
            account_id=scope.account_id,
            projected=projected,
            existing=existing,
            event_observed_at=observed_at,
        )

    async def _converged_segment(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        closed: ProjectedSegment,
        observed_at: datetime,
    ) -> ActivityTimelineSegment | None:
        old = await self._segments.find_account_segment_by_first_event(
            db_session, account_id=account_id, first_event_id=closed.first_event_id
        )
        if (
            old is None
            or closed.converged_index([old], observed_at=observed_at) is None
        ):
            return None
        return old


def _conflict_status(record: ActivityRecord, stored: ActivityRecord) -> str:
    """Result of a record whose event ID or device sequence is already stored."""
    if stored.event_id != record.event_id:
        return "sequence_rejected"
    if stored.device_id == record.device_id:
        return "duplicate"
    return "event_id_rejected"


class ProcessActivityBatch(UseCase):
    """Consumer path: one transaction per delivered batch message (ADR 0006, 0007)."""

    def __init__(
        self, *, devices: DeviceDirectory, dispatcher: EventDispatcher, clock: Clock
    ) -> None:
        self._processor = ActivityBatchProcessor(clock=clock, devices=devices)
        self._dispatcher = dispatcher

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, batch: ActivityBatch
    ) -> list[dict[str, str]]:
        """Process the batch once; a redelivery returns the stored results.

        Raises `TerminalBatchError` with a safe diagnostic code.
        """
        async with unit_of_work(db_session, self._dispatcher):
            return await self._processor.process(
                db_session, account_id=account_id, batch=batch
            )


class ProcessLegacyActivityBatch(UseCase):
    """Direct path: holds the account timeline lock, unlike the Consumer path."""

    def __init__(
        self, *, devices: DeviceDirectory, dispatcher: EventDispatcher, clock: Clock
    ) -> None:
        self._processor = ActivityBatchProcessor(clock=clock, devices=devices)
        self._dispatcher = dispatcher
        self._segments = TimelineSegmentRepository()

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, batch: ActivityBatch
    ) -> list[dict[str, str]]:
        """Process the batch under the account timeline lock.

        Raises `ActivityTimelineBusyError` and `TerminalBatchError`.
        """
        async with unit_of_work(db_session, self._dispatcher):
            await self._segments.lock_timeline(db_session, account_id)
            return await self._processor.process(
                db_session, account_id=account_id, batch=batch
            )


class AcceptActivityBatch(UseCase):
    """Hands a batch of an owned device to delivery without processing it."""

    def __init__(self, *, devices: DeviceDirectory) -> None:
        self._devices = devices

    async def execute[B](
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        device_id: UUID,
        batch: B,
        publisher: ActivityBatchPublisher[B] | None,
    ) -> None:
        """Raise `ActivityDeviceNotFoundError` for a device the account does not
        own and `ActivityDeliveryUnavailableError` when delivery is not confirmed.
        """
        async with read_snapshot(db_session):
            owned = await self._devices.find_owned(
                db_session, account_id=account_id, device_id=device_id
            )
        if owned is None:
            raise ActivityDeviceNotFoundError
        if publisher is None:
            raise ActivityDeliveryUnavailableError
        await publisher.publish(account_id, batch)


class BatchFailureHandler:
    """Future failure boundary. No storage, blocking, retry or ACK policy exists."""

    async def handle(self) -> None:
        raise NotImplementedError("batch failure policy is not implemented")


class BatchRecoveryService:
    """Future recovery boundary; never report a successful recovery."""

    async def recover(self) -> None:
        raise NotImplementedError("batch recovery policy is not implemented")
