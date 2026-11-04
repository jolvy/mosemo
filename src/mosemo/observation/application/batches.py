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


class ActivityBatchProcessor:
    """Processes one batch in the transaction that its use case opened.

    Built per call with that use case's session.
    """

    def __init__(
        self, db_session: AsyncSession, *, clock: Clock, devices: DeviceDirectory
    ) -> None:
        self.session = db_session
        self._clock = clock
        self._devices = devices
        self._records = ActivityRecordRepository()
        self._segments = TimelineSegmentRepository()
        self._receipts = BatchReceiptRepository()
        self._focus_sessions = FocusSessionLinks(
            focus_sessions=FocusSessionRepository()
        )

    async def process(
        self,
        account_id: UUID,
        batch: ActivityBatch,
    ) -> list[dict[str, str]]:
        now = self._clock()
        await self._ensure_device_owned(account_id, batch.device_id)
        initialized = await self._receipts.has_for_account(self.session, account_id)
        claimed = await self._receipts.claim(
            self.session,
            account_id=account_id,
            device_id=batch.device_id,
            batch_id=batch.batch_id,
            fingerprint=batch.fingerprint,
        )
        if claimed is None:
            return await self._winner_results(account_id, batch)
        device_ids = await self._devices.list_device_ids(self.session, account_id)
        latest = await self._initialize_account(
            account_id, device_ids, initialized=initialized, now=now
        )
        adopted, results = await self._store_records(account_id, batch)
        await self._ensure_device_order(adopted)
        if adopted:
            try:
                await self._project(
                    account_id, adopted, device_ids=device_ids, latest=latest, now=now
                )
            except ProjectionError:
                raise TerminalBatchError("projection_time_regression") from None
        claimed.results = results
        await self.session.flush()
        return results

    async def _ensure_device_owned(self, account_id: UUID, device_id: UUID) -> None:
        owned = await self._devices.find_owned(
            self.session, account_id=account_id, device_id=device_id
        )
        if owned is None:
            raise TerminalBatchError("device_not_owned")

    async def _winner_results(
        self, account_id: UUID, batch: ActivityBatch
    ) -> list[dict[str, str]]:
        winner = await self._receipts.find(
            self.session,
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
        self,
        account_id: UUID,
        device_ids: Sequence[UUID],
        *,
        initialized: bool,
        now: datetime,
    ) -> ActivityRecord | None:
        if not initialized and not await self._records.is_account_clock_monotone(
            self.session, device_ids
        ):
            raise TerminalBatchError("projection_time_regression")
        latest = await self._records.find_latest_account_record(
            self.session, device_ids
        )
        if not initialized and latest is not None:
            await self._rebuild_legacy_projection(
                account_id, latest, device_ids=device_ids, now=now
            )
        return latest

    async def _store_records(
        self, account_id: UUID, batch: ActivityBatch
    ) -> tuple[list[ActivityRecord], list[dict[str, str]]]:
        adopted = []
        results = []
        for record in batch.records:
            stored, status = await self._store_record(account_id, record)
            if stored is not None:
                adopted.append(stored)
            results.append({"eventId": str(record.event_id), "status": status})
        return adopted, results

    async def _store_record(
        self, account_id: UUID, record: ActivityRecord
    ) -> tuple[ActivityRecord | None, str]:
        # Processed IDs are immutable. Pre-reads do not replace INSERT's
        # unique constraints: a concurrent winner comes back from get_or_create.
        status = await self._rejection_status(record)
        if status is not None:
            return None, status
        await self._validate_focus_session(account_id, record)
        stored, created = await self._records.get_or_create(self.session, record)
        if created:
            return stored, "stored"
        return None, _conflict_status(record, stored)

    async def _rejection_status(self, record: ActivityRecord) -> str | None:
        event = await self._records.find_by_event_id(self.session, record.event_id)
        if event is None:
            event = await self._records.find_by_device_sequence(
                self.session, device_id=record.device_id, sequence=record.sequence
            )
        return None if event is None else _conflict_status(record, event)

    async def _validate_focus_session(
        self, account_id: UUID, record: ActivityRecord
    ) -> None:
        try:
            await self._focus_sessions.validate(
                self.session, account_id=account_id, record=record
            )
        except (
            FocusSessionNotFoundError,
            FocusSessionInvalidTimeError,
        ) as error:
            raise TerminalBatchError(type(error).__name__) from None

    async def _ensure_device_order(self, adopted: list[ActivityRecord]) -> None:
        for record in adopted:
            previous = await self._records.find_previous_device_observed_at(
                self.session, record
            )
            following = await self._records.find_next_device_observed_at(
                self.session, record
            )
            if not record.is_in_device_order(previous=previous, following=following):
                raise TerminalBatchError("projection_time_regression")

    async def _rebuild_legacy_projection(
        self,
        account_id: UUID,
        latest: ActivityRecord,
        *,
        device_ids: Sequence[UUID],
        now: datetime,
    ) -> None:
        # The first receipt marks an atomic transition from received_at tie order.
        # Rebuild even for duplicate-only batches; an unchanged prefix does not
        # prove that later legacy ties already follow the new ordering.
        start = datetime.min.replace(tzinfo=UTC)
        projection = TimelineProjection()
        after = None
        while page := await self._records.list_record_page(
            self.session, device_ids, start=start, after=after
        ):
            for record in page:
                projection.feed(record)
            after = observation_key(page[-1])
        existing = await self._segments.list_segment_range(
            self.session, account_id, start=start
        )
        await self._segments.replace_account_segments(
            self.session,
            account_id=account_id,
            projected=projection.finish(now),
            existing=existing,
            # Keep replacement from converging before every legacy tie is rebuilt.
            event_observed_at=latest.observed_at,
        )

    async def _project(
        self,
        account_id: UUID,
        adopted: list[ActivityRecord],
        *,
        device_ids: Sequence[UUID],
        latest: ActivityRecord | None,
        now: datetime,
    ) -> None:
        ordered = sorted(adopted, key=observation_key)
        if latest is None or observation_key(ordered[0]) > observation_key(latest):
            last_segment = await self._segments.find_last_account_segment(
                self.session, account_id
            )
            if last_segment is not None:
                await self._append(
                    account_id, ordered, last_segment=last_segment, now=now
                )
                return
        await self._replay(account_id, ordered, device_ids=device_ids, now=now)

    async def _append(
        self,
        account_id: UUID,
        ordered: list[ActivityRecord],
        *,
        last_segment: ActivityTimelineSegment,
        now: datetime,
    ) -> None:
        projection = TimelineProjection(segment_seed(last_segment))
        for record in ordered:
            projection.feed(record)
        await self._segments.replace_account_segments(
            self.session,
            account_id=account_id,
            projected=projection.finish(now),
            existing=[last_segment],
            event_observed_at=ordered[-1].observed_at,
        )

    async def _replay(
        self,
        account_id: UUID,
        ordered: list[ActivityRecord],
        *,
        device_ids: Sequence[UUID],
        now: datetime,
    ) -> None:
        observed_at = ordered[-1].observed_at
        start = await self._segments.find_replay_start(
            self.session, account_id, observed_at=ordered[0].observed_at
        )
        projection = TimelineProjection()
        after = None
        remaining = {r.event_id for r in ordered}
        convergence = None
        while convergence is None and (
            page := await self._records.list_record_page(
                self.session, device_ids, start=start, after=after
            )
        ):
            for record in page:
                remaining.discard(record.event_id)
                closed = projection.feed(record)
                if closed is not None and not remaining:
                    convergence = await self._converged_segment(
                        account_id, closed, observed_at=observed_at
                    )
                    if convergence is not None:
                        break
            after = observation_key(page[-1])
        existing = await self._segments.list_segment_range(
            self.session,
            account_id,
            start=start,
            through=convergence.started_at if convergence else None,
        )
        projected = projection.segments[:-1] if convergence else projection.finish(now)
        await self._segments.replace_account_segments(
            self.session,
            account_id=account_id,
            projected=projected,
            existing=existing,
            event_observed_at=observed_at,
        )

    async def _converged_segment(
        self, account_id: UUID, closed: ProjectedSegment, *, observed_at: datetime
    ) -> ActivityTimelineSegment | None:
        old = await self._segments.find_account_segment_by_first_event(
            self.session, account_id=account_id, first_event_id=closed.first_event_id
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
        self._devices = devices
        self._dispatcher = dispatcher
        self._clock = clock

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, batch: ActivityBatch
    ) -> list[dict[str, str]]:
        """Process the batch once; a redelivery returns the stored results.

        Raises `TerminalBatchError` with a safe diagnostic code.
        """
        async with unit_of_work(db_session, self._dispatcher):
            return await ActivityBatchProcessor(
                db_session, clock=self._clock, devices=self._devices
            ).process(account_id, batch)


class ProcessLegacyActivityBatch(UseCase):
    """Direct path: holds the account timeline lock, unlike the Consumer path."""

    def __init__(
        self, *, devices: DeviceDirectory, dispatcher: EventDispatcher, clock: Clock
    ) -> None:
        self._devices = devices
        self._dispatcher = dispatcher
        self._clock = clock
        self._segments = TimelineSegmentRepository()

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, batch: ActivityBatch
    ) -> list[dict[str, str]]:
        """Process the batch under the account timeline lock.

        Raises `ActivityTimelineBusyError` and `TerminalBatchError`.
        """
        async with unit_of_work(db_session, self._dispatcher):
            await self._segments.lock_timeline(db_session, account_id)
            return await ActivityBatchProcessor(
                db_session, clock=self._clock, devices=self._devices
            ).process(account_id, batch)


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
