"""Batch processing within a transaction owned by the calling Executor."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.enums import SegmentType
from mosemo.activities.messages import TerminalBatchError
from mosemo.activities.models import ActivityRecord as StoredActivityRecord
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.receipt_repository import BatchReceiptRepository
from mosemo.activities.repository import ActivityRepository, converged_old_index
from mosemo.activities.schemas import ActivityBatchRequest
from mosemo.activities.timeline import (
    ProjectedActivity,
    ProjectedGap,
    ProjectedSegment,
    ProjectionFailure,
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
        assert (
            segment.last_event_id is not None
            and segment.last_observed_at is not None
            and segment.context is not None
        )
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

    async def process(
        self,
        account_id: UUID,
        batch: ActivityBatchRequest,
    ) -> list[dict[str, str]]:
        now = self._clock()
        device = await DeviceRepository(self.session).find_owned_by_id(
            account_id=account_id, device_id=batch.device_id
        )
        if device is None:
            raise TerminalBatchError("device_not_owned")
        initialized = await self.receipts.has_for_account(account_id)
        fingerprint = batch_fingerprint(batch)
        claimed = await self.receipts.claim(
            account_id=account_id,
            device_id=batch.device_id,
            batch_id=batch.batch_id,
            fingerprint=fingerprint,
        )
        if claimed is None:
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
        if not initialized and not await self.repository.account_clock_is_monotone(
            account_id
        ):
            raise TerminalBatchError("projection_time_regression")
        latest = await self.repository.latest_account_record(account_id)
        if not initialized and latest is not None:
            await self._rebuild_legacy_projection(account_id, latest, now)
        adopted = []
        results = []
        for record in batch.activity_records():
            # Processed IDs are immutable. Pre-reads do not replace INSERT's
            # unique constraints: concurrent winners are still reread below.
            event = await self.repository.find_by_event_id(record.event_id)
            if event is not None:
                results.append(
                    {
                        "eventId": str(record.event_id),
                        "status": "duplicate"
                        if event.device_id == batch.device_id
                        else "event_id_rejected",
                    }
                )
                continue
            sequence = await self.repository.find_by_device_sequence(
                device_id=batch.device_id, sequence=record.sequence
            )
            if sequence is not None:
                results.append(
                    {"eventId": str(record.event_id), "status": "sequence_rejected"}
                )
                continue
            if record.focus_session_id is not None:
                focus = await FocusSessionRepository(self.session).find_owned(
                    account_id, record.focus_session_id
                )
                try:
                    if focus is None:
                        raise FocusSessionNotFoundError
                    focus.validate_activity(
                        account_id=account_id,
                        device_id=batch.device_id,
                        observed_at=record.observed_at,
                    )
                except (
                    FocusSessionNotFoundError,
                    FocusSessionInvalidTimeError,
                ) as error:
                    raise TerminalBatchError(type(error).__name__) from None
            stored = await self.repository.insert(record)
            if stored is None:
                event = await self.repository.find_by_event_id(record.event_id)
                if event is not None:
                    status = (
                        "duplicate"
                        if event.device_id == batch.device_id
                        else "event_id_rejected"
                    )
                else:
                    sequence = await self.repository.find_by_device_sequence(
                        device_id=batch.device_id, sequence=record.sequence
                    )
                    if sequence is None:
                        raise RuntimeError("record winner missing")
                    status = "sequence_rejected"
            else:
                adopted.append(stored)
                status = "stored"
            results.append({"eventId": str(record.event_id), "status": status})
        for record in adopted:
            if not await self.repository.validate_device_clock(record):
                raise TerminalBatchError("projection_time_regression")
        if adopted:
            try:
                await self._project(account_id, adopted, latest, now)
            except ProjectionFailure:
                raise TerminalBatchError("projection_time_regression") from None
        claimed.results = results
        await self.session.flush()
        return results

    async def _rebuild_legacy_projection(
        self, account_id: UUID, latest: StoredActivityRecord, now: datetime
    ) -> None:
        # The first receipt marks an atomic transition from received_at tie order.
        # Rebuild even for duplicate-only batches; an unchanged prefix does not
        # prove that later legacy ties already follow the new ordering.
        start = datetime.min.replace(tzinfo=UTC)
        projection = TimelineProjection()
        after = None
        while page := await self.repository.record_page(
            account_id, start=start, after=after
        ):
            for record in page:
                projection.feed(record)
            after = observation_key(page[-1])
        existing = await self.repository.segment_range(account_id, start=start)
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
        latest: StoredActivityRecord | None,
        now: datetime,
    ) -> None:
        ordered = sorted(adopted, key=observation_key)
        append = latest is None or observation_key(ordered[0]) > observation_key(latest)
        last_segment = (
            await self.repository.last_account_segment(account_id) if append else None
        )
        if append and last_segment is not None:
            projection = TimelineProjection(segment_seed(last_segment))
            for record in ordered:
                projection.feed(record)
            await self.repository.replace_account_segments(
                account_id=account_id,
                projected=projection.finish(now),
                existing=[last_segment],
                event_observed_at=ordered[-1].observed_at,
            )
            return
        start = await self.repository.find_replay_start(
            account_id, ordered[0].observed_at
        )
        projection = TimelineProjection()
        after = None
        remaining = {r.event_id for r in ordered}
        convergence = None
        while True:
            page = await self.repository.record_page(
                account_id, start=start, after=after
            )
            if not page:
                break
            for record in page:
                remaining.discard(record.event_id)
                closed = projection.feed(record)
                if closed is not None and not remaining:
                    old = await self.repository.find_account_segment_by_first_event(
                        account_id=account_id, first_event_id=closed.first_event_id
                    )
                    if (
                        old is not None
                        and converged_old_index([old], closed, ordered[-1].observed_at)
                        is not None
                    ):
                        convergence = old
                        break
            if convergence is not None:
                break
            after = observation_key(page[-1])
        existing = await self.repository.segment_range(
            account_id,
            start=start,
            through=convergence.started_at if convergence else None,
        )
        projected = projection.segments[:-1] if convergence else projection.finish(now)
        await self.repository.replace_account_segments(
            account_id=account_id,
            projected=projected,
            existing=existing,
            event_observed_at=ordered[-1].observed_at,
        )
