"""Seeds analytics source rows (segments, confirmations, sessions) for one account.

Times are minutes from 2026-11-03 00:00 Asia/Seoul. Rows are added to the given
session; the caller commits.
"""

from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.infrastructure.tables import (
    analytics_daily_markers,
    analytics_daily_stats,
)
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.devices import Device
from mosemo.labeling.domain.confirmations import ActivityLabelConfirmation
from mosemo.labels.domain.labels import Label
from mosemo.observation.domain.activities import ActivityRecord, RecordType
from mosemo.observation.domain.focus_sessions import FocusSession, FocusSessionPause
from mosemo.observation.domain.timeline import (
    ActivityTimelineSegment,
    SegmentType,
    segment_version,
)
from mosemo.observation.infrastructure.tables import (
    activity_timeline_segments,
    focus_sessions,
)
from mosemo.shared.domain.timezones import Timezone
from tests.support.identity import delete_accounts

BASE = datetime(2026, 11, 3, tzinfo=ZoneInfo("Asia/Seoul"))
OPAQUE: dict[str, Any] = {"kind": "opaque"}
_sequences = count()


def at(minutes: float) -> datetime:
    return (BASE + timedelta(minutes=minutes)).astimezone(UTC)


def detailed(
    bundle_id: str | None,
    *,
    name: str | None = None,
    url: str | None = None,
    title: str = "창",
) -> dict[str, Any]:
    """A detailed context; `bundle_id` None is an unknown app, `url` a browser."""
    bundle = (
        {"status": "captured", "value": bundle_id}
        if bundle_id is not None
        else {"status": "absent"}
    )
    web: dict[str, Any] = {"kind": "not_applicable"}
    if url is not None:
        web = {
            "kind": "browser",
            "tabTitle": {"status": "captured", "value": title},
            "url": {"status": "captured", "value": url},
        }
    return {
        "kind": "detailed",
        "app": {
            "bundleId": bundle,
            "name": {"status": "captured", "value": name or bundle_id or "?"},
        },
        "window": {
            "status": "captured",
            "title": {"status": "captured", "value": title},
        },
        "web": web,
    }


class SourceSeed:
    """One Seoul account with a device and two labels, and its source rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        # Added after the records: the unit of work does not order the two tables.
        self.confirmations: list[ActivityLabelConfirmation] = []
        self.account_id: UUID
        self.device_id: UUID
        self.coding: UUID
        self.leisure: UUID

    async def create_account(self) -> None:
        account = Account(
            provider=AccountProvider.KAKAO,
            provider_subject=f"analytics-{uuid4()}",
        )
        self.session.add(account)
        await self.session.flush()
        device = Device(account_id=account.id, idempotency_key=uuid4())
        coding = Label(account_id=account.id, display_name="코딩")
        leisure = Label(account_id=account.id, display_name="여가")
        self.session.add_all([device, coding, leisure])
        await self.session.flush()
        self.account_id = account.id
        self.device_id = device.id
        self.coding = coding.id
        self.leisure = leisure.id

    def record(self, observed_at: datetime) -> ActivityRecord:
        record = ActivityRecord(
            event_id=uuid4(),
            device_id=self.device_id,
            sequence=next(_sequences),
            record_type=RecordType.ACTIVITY_OBSERVATION,
            observed_at=observed_at,
            timezone_id=Timezone.ASIA_SEOUL,
            utc_offset_minutes=540,
            payload={"context": OPAQUE},
        )
        self.session.add(record)
        return record

    def activity(
        self,
        start: float,
        end: float | None,
        context: dict[str, Any],
        *,
        last: float | None = None,
        segment_id: UUID | None = None,
    ) -> ActivityTimelineSegment:
        """A closed activity, or an open one (`end` None) last observed at `last`."""
        observed = last if last is not None else end
        assert observed is not None, "an open activity needs `last`"
        last_observed_at = at(observed)
        first = self.record(at(start))
        final = self.record(last_observed_at)
        segment = ActivityTimelineSegment(
            account_id=self.account_id,
            segment_type=SegmentType.ACTIVITY,
            started_at=at(start),
            ended_at=None if end is None else at(end),
            first_event_id=first.event_id,
            last_event_id=final.event_id,
            last_observed_at=last_observed_at,
            context=context,
        )
        if segment_id is not None:
            segment.id = segment_id
        self.session.add(segment)
        return segment

    def gap(self, start: float, end: float | None) -> ActivityTimelineSegment:
        first = self.record(at(start))
        segment = ActivityTimelineSegment(
            account_id=self.account_id,
            segment_type=SegmentType.CAPTURE_GAP,
            started_at=at(start),
            ended_at=None if end is None else at(end),
            first_event_id=first.event_id,
            reason="user_paused",
        )
        self.session.add(segment)
        return segment

    def confirm(
        self, segment: ActivityTimelineSegment, label_id: UUID | None
    ) -> ActivityLabelConfirmation:
        """A confirmation of the closed segment's current version, added by `flush`."""
        assert segment.ended_at is not None
        confirmation = ActivityLabelConfirmation(
            account_id=self.account_id,
            first_event_id=segment.first_event_id,
            segment_version=segment_version(segment, ended_at=segment.ended_at),
            label_id=label_id,
            confirmed_at=segment.ended_at,
            updated_at=segment.ended_at,
        )
        self.confirmations.append(confirmation)
        return confirmation

    async def flush(self) -> None:
        await self.session.flush()
        self.session.add_all(self.confirmations)
        self.confirmations.clear()
        await self.session.flush()

    def focus(
        self,
        start: float,
        end: float | None,
        *,
        label_id: UUID | None = None,
        pauses: list[tuple[float, float]] | None = None,
    ) -> FocusSession:
        """A completed session, or one in progress when `end` is None."""
        completed = end is not None
        focus = FocusSession(
            id=uuid4(),
            account_id=self.account_id,
            device_id=self.device_id,
            started_at=at(start),
            ended_at=None if end is None else at(end),
            target_seconds=0,
            work_seconds=0 if completed else None,
            label_id=(label_id or self.coding) if completed else None,
            pauses_reported=pauses is not None,
            pauses=[
                FocusSessionPause(started_at=at(low), ended_at=at(high))
                for low, high in pauses or []
            ],
        )
        self.session.add(focus)
        return focus


async def delete_seeds(session: AsyncSession, *account_ids: UUID) -> None:
    for table in (
        analytics_daily_markers,
        analytics_daily_stats,
        activity_timeline_segments,
        focus_sessions,
    ):
        await session.execute(delete(table).where(table.c.account_id.in_(account_ids)))
    await session.execute(delete_accounts(*account_ids))
    await session.commit()
