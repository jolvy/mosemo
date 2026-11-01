"""Batch processing within a transaction owned by the calling Executor."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.enums import SegmentType
from mosemo.activities.mappers import ActivityRecordMapper
from mosemo.activities.messages import TerminalBatchError
from mosemo.activities.models import ActivityRecord as StoredActivityRecord
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.receipt_repository import BatchReceiptRepository
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.schemas import ActivityBatchRequest
from mosemo.activities.schemas import ActivityRecord as ActivityRecordRequest
from mosemo.activities.timeline import (
    ProjectedActivity,
    ProjectedGap,
    ProjectedSegment,
    ProjectionError,
    TimelineProjection,
    observation_key,
)
from mosemo.clock import Clock
from mosemo.devices.repository import DeviceRepository
from mosemo.focus_sessions.models import (
    FocusSessionInvalidTimeError,
    FocusSessionNotFoundError,
)
from mosemo.focus_sessions.repository import FocusSessionRepository


def batch_fingerprint(batch: ActivityBatchRequest) -> str:
    canonical = json.dumps(
        batch.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(canonical.encode()).hexdigest()


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
    def __init__(self, session: AsyncSession, *, clock: Clock):
        self.session = session
        self._clock = clock
        self.repository = ActivityRepository(session)
        self.receipts = BatchReceiptRepository(session)
        self._mapper = ActivityRecordMapper()

    async def process(
        self,
        account_id: UUID,
        batch: ActivityBatchRequest,
    ) -> list[dict[str, str]]:
        now = self._clock()
        await self._ensure_device_owned(account_id, batch.device_id)
        initialized = await self.receipts.has_for_account(account_id)
        fingerprint = batch_fingerprint(batch)
        claimed = await self.receipts.claim(
            account_id=account_id,
            device_id=batch.device_id,
            batch_id=batch.batch_id,
            fingerprint=fingerprint,
        )
        if claimed is None:
            return await self._winner_results(
                account_id, batch, fingerprint=fingerprint
            )
        latest = await self._initialize_account(
            account_id, initialized=initialized, now=now
        )
        adopted, results = await self._store_records(account_id, batch)
        await self._ensure_device_order(adopted)
        if adopted:
            try:
                await self._project(account_id, adopted, latest=latest, now=now)
            except ProjectionError:
                raise TerminalBatchError("projection_time_regression") from None
        claimed.results = results
        await self.session.flush()
        return results

    async def _ensure_device_owned(self, account_id: UUID, device_id: UUID) -> None:
        device = await DeviceRepository(self.session).find_owned_by_id(
            account_id=account_id, device_id=device_id
        )
        if device is None:
            raise TerminalBatchError("device_not_owned")

    async def _winner_results(
        self, account_id: UUID, batch: ActivityBatchRequest, *, fingerprint: str
    ) -> list[dict[str, str]]:
        winner = await self.receipts.find(
            account_id=account_id,
            device_id=batch.device_id,
            batch_id=batch.batch_id,
        )
        if winner is None:
            raise RuntimeError("receipt winner missing")
        if winner.fingerprint != fingerprint:
            raise TerminalBatchError("batch_content_conflict")
        return winner.results

    async def _initialize_account(
        self, account_id: UUID, *, initialized: bool, now: datetime
    ) -> StoredActivityRecord | None:
        if not initialized and not await self.repository.is_account_clock_monotone(
            account_id
        ):
            raise TerminalBatchError("projection_time_regression")
        latest = await self.repository.find_latest_account_record(account_id)
        if not initialized and latest is not None:
            await self._rebuild_legacy_projection(account_id, latest, now=now)
        return latest

    async def _store_records(
        self, account_id: UUID, batch: ActivityBatchRequest
    ) -> tuple[list[StoredActivityRecord], list[dict[str, str]]]:
        adopted = []
        results = []
        for record in batch.activity_records():
            stored, status = await self._store_record(account_id, record)
            if stored is not None:
                adopted.append(stored)
            results.append({"eventId": str(record.event_id), "status": status})
        return adopted, results

    async def _store_record(
        self, account_id: UUID, record: ActivityRecordRequest
    ) -> tuple[StoredActivityRecord | None, str]:
        # Processed IDs are immutable. Pre-reads do not replace INSERT's
        # unique constraints: concurrent winners are still reread below.
        status = await self._rejection_status(record)
        if status is not None:
            return None, status
        await self._validate_focus_session(account_id, record)
        stored = await self.repository.add_if_absent(self._mapper.to_entity(record))
        if stored is not None:
            return stored, "stored"
        status = await self._rejection_status(record)
        if status is None:
            raise RuntimeError("record winner missing")
        return None, status

    async def _rejection_status(self, record: ActivityRecordRequest) -> str | None:
        event = await self.repository.find_by_event_id(record.event_id)
        if event is not None:
            if event.device_id == record.device_id:
                return "duplicate"
            return "event_id_rejected"
        sequence = await self.repository.find_by_device_sequence(
            device_id=record.device_id, sequence=record.sequence
        )
        if sequence is not None:
            return "sequence_rejected"
        return None

    async def _validate_focus_session(
        self, account_id: UUID, record: ActivityRecordRequest
    ) -> None:
        if record.focus_session_id is None:
            return
        focus = await FocusSessionRepository(self.session).find_owned(
            account_id, record.focus_session_id
        )
        try:
            if focus is None:
                raise FocusSessionNotFoundError
            focus.validate_activity(
                account_id=account_id,
                device_id=record.device_id,
                observed_at=record.observed_at,
            )
        except (
            FocusSessionNotFoundError,
            FocusSessionInvalidTimeError,
        ) as error:
            raise TerminalBatchError(type(error).__name__) from None

    async def _ensure_device_order(self, adopted: list[StoredActivityRecord]) -> None:
        for record in adopted:
            previous = await self.repository.find_previous_device_observed_at(record)
            following = await self.repository.find_next_device_observed_at(record)
            if not record.is_in_device_order(previous=previous, following=following):
                raise TerminalBatchError("projection_time_regression")

    async def _rebuild_legacy_projection(
        self, account_id: UUID, latest: StoredActivityRecord, *, now: datetime
    ) -> None:
        # The first receipt marks an atomic transition from received_at tie order.
        # Rebuild even for duplicate-only batches; an unchanged prefix does not
        # prove that later legacy ties already follow the new ordering.
        start = datetime.min.replace(tzinfo=UTC)
        projection = TimelineProjection()
        after = None
        while page := await self.repository.list_record_page(
            account_id, start=start, after=after
        ):
            for record in page:
                projection.feed(record)
            after = observation_key(page[-1])
        existing = await self.repository.list_segment_range(account_id, start=start)
        await self.repository.replace_account_segments(
            account_id=account_id,
            projected=projection.finish(now),
            existing=existing,
            # Keep replacement from converging before every legacy tie is rebuilt.
            event_observed_at=latest.observed_at,
        )

    async def _project(
        self,
        account_id: UUID,
        adopted: list[StoredActivityRecord],
        *,
        latest: StoredActivityRecord | None,
        now: datetime,
    ) -> None:
        ordered = sorted(adopted, key=observation_key)
        if latest is None or observation_key(ordered[0]) > observation_key(latest):
            last_segment = await self.repository.find_last_account_segment(account_id)
            if last_segment is not None:
                await self._append(
                    account_id, ordered, last_segment=last_segment, now=now
                )
                return
        await self._replay(account_id, ordered, now=now)

    async def _append(
        self,
        account_id: UUID,
        ordered: list[StoredActivityRecord],
        *,
        last_segment: ActivityTimelineSegment,
        now: datetime,
    ) -> None:
        projection = TimelineProjection(segment_seed(last_segment))
        for record in ordered:
            projection.feed(record)
        await self.repository.replace_account_segments(
            account_id=account_id,
            projected=projection.finish(now),
            existing=[last_segment],
            event_observed_at=ordered[-1].observed_at,
        )

    async def _replay(
        self,
        account_id: UUID,
        ordered: list[StoredActivityRecord],
        *,
        now: datetime,
    ) -> None:
        observed_at = ordered[-1].observed_at
        start = await self.repository.find_replay_start(
            account_id, ordered[0].observed_at
        )
        projection = TimelineProjection()
        after = None
        remaining = {r.event_id for r in ordered}
        convergence = None
        while convergence is None and (
            page := await self.repository.list_record_page(
                account_id, start=start, after=after
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
        existing = await self.repository.list_segment_range(
            account_id,
            start=start,
            through=convergence.started_at if convergence else None,
        )
        projected = projection.segments[:-1] if convergence else projection.finish(now)
        await self.repository.replace_account_segments(
            account_id=account_id,
            projected=projected,
            existing=existing,
            event_observed_at=observed_at,
        )

    async def _converged_segment(
        self, account_id: UUID, closed: ProjectedSegment, *, observed_at: datetime
    ) -> ActivityTimelineSegment | None:
        old = await self.repository.find_account_segment_by_first_event(
            account_id=account_id, first_event_id=closed.first_event_id
        )
        if (
            old is None
            or closed.converged_index([old], observed_at=observed_at) is None
        ):
            return None
        return old
