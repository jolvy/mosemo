"""`LabelConfirmed` from label confirmation and the confirmation directory."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.bootstrap import create_device_directory
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.devices import Device
from mosemo.labeling.application.confirmations import (
    ConfirmSegmentLabel,
    ConfirmSegmentLabels,
    LabelSelection,
    SegmentConfirmations,
)
from mosemo.labeling.bootstrap import create_label_confirmation_directory
from mosemo.labeling.infrastructure.confirmations import ConfirmationRepository
from mosemo.labeling.infrastructure.proposals import ProposalRepository
from mosemo.labeling.public import LabelConfirmationView, LabelConfirmed
from mosemo.labels.bootstrap import create_label_catalog
from mosemo.labels.domain.labels import Label
from mosemo.observation.application.activities import RecordActivity
from mosemo.observation.bootstrap import create_observation_timeline
from mosemo.observation.domain.activities import ActivityRecord, RecordType
from mosemo.observation.public import ObservedSegment
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.domain.timezones import Timezone
from tests.support.clock import FakeClock

T = datetime(2026, 9, 14, tzinfo=UTC)
OPAQUE: dict[str, object] = {"kind": "opaque"}


def detailed(name: str) -> dict[str, object]:
    return {
        "kind": "detailed",
        "app": {
            "bundleId": {"status": "absent"},
            "name": {"status": "captured", "value": name},
        },
        "window": {"status": "absent"},
        "web": {"kind": "not_applicable"},
    }


def observation(
    device_id: UUID, sequence: int, seconds: int, context: dict[str, object]
) -> ActivityRecord:
    return ActivityRecord(
        event_id=uuid4(),
        device_id=device_id,
        sequence=sequence,
        record_type=RecordType.ACTIVITY_OBSERVATION,
        observed_at=T + timedelta(seconds=seconds),
        timezone_id=Timezone.ASIA_SEOUL,
        utc_offset_minutes=540,
        payload={"context": context},
    )


@pytest.mark.asyncio
async def test_label_confirmed_only_when_confirmation_is_created_or_changed(
    integration_session: AsyncSession,
) -> None:
    session = integration_session
    account = Account(provider=AccountProvider.KAKAO, provider_subject=f"lc-{uuid4()}")
    session.add(account)
    await session.flush()
    device = Device(account_id=account.id, idempotency_key=uuid4())
    first_label = Label(account_id=account.id, display_name="코딩")
    second_label = Label(account_id=account.id, display_name="독서")
    session.add_all([device, first_label, second_label])
    await session.commit()

    clock = FakeClock(T + timedelta(seconds=35))
    record = RecordActivity(
        devices=create_device_directory(), dispatcher=EventDispatcher(), clock=clock
    )
    # A closed A segment [T, T+20] and an open B segment observed at T+30.
    for sequence, seconds, context in [
        (1, 0, detailed("A")),
        (2, 10, detailed("A")),
        (3, 20, OPAQUE),
        (4, 30, detailed("B")),
    ]:
        await record.execute(
            session,
            account_id=account.id,
            record=observation(device.id, sequence, seconds, context),
        )
    clock.now = T + timedelta(hours=1)

    timeline = create_observation_timeline()
    segments = await timeline.read_range(
        session, account.id, start=T, end=T + timedelta(minutes=1), now=clock.now
    )
    await session.commit()
    closed, open_b = (s for s in segments if s.context != OPAQUE)
    assert open_b.ended_at == open_b.last_observed_at == T + timedelta(seconds=30)

    dispatcher = EventDispatcher()
    seen: list[LabelConfirmed] = []

    async def collect(event: LabelConfirmed, _: AsyncSession) -> None:
        seen.append(event)

    dispatcher.subscribe(LabelConfirmed, collect)
    confirmations = SegmentConfirmations(
        confirmations=ConfirmationRepository(), proposals=ProposalRepository()
    )
    dependencies = {
        "confirmations": confirmations,
        "labels": create_label_catalog(),
        "timeline": timeline,
        "dispatcher": dispatcher,
        "clock": clock,
    }
    confirm = ConfirmSegmentLabel(**dependencies)
    confirm_many = ConfirmSegmentLabels(**dependencies)

    def selection(segment: ObservedSegment, label_id: UUID | None) -> LabelSelection:
        assert segment.version is not None
        return LabelSelection(
            segment_id=segment.id, segment_version=segment.version, label_id=label_id
        )

    closed_event = LabelConfirmed(
        account_id=account.id,
        segment_started_at=T,
        segment_ended_at=T + timedelta(seconds=20),
    )
    for label_id, expected in [
        (first_label.id, [closed_event]),
        (first_label.id, []),
        (second_label.id, [closed_event]),
        (None, [closed_event]),
        (None, []),
    ]:
        seen.clear()
        await confirm.execute(
            session, account_id=account.id, selection=selection(closed, label_id)
        )
        assert seen == expected, label_id

    open_event = LabelConfirmed(
        account_id=account.id,
        segment_started_at=T + timedelta(seconds=30),
        segment_ended_at=T + timedelta(seconds=30),
    )
    seen.clear()
    batch = [selection(open_b, first_label.id), selection(closed, None)]
    await confirm_many.execute(session, account_id=account.id, selections=batch)
    assert seen == [open_event]
    seen.clear()
    await confirm_many.execute(session, account_id=account.id, selections=batch)
    assert seen == []

    found = await create_label_confirmation_directory().find_by_first_events(
        session,
        account.id,
        first_event_ids=[closed.first_event_id, open_b.first_event_id, uuid4()],
    )
    assert sorted(found, key=lambda view: view.segment_version) == sorted(
        [
            LabelConfirmationView(
                first_event_id=closed.first_event_id,
                segment_version=closed.version or "",
                label_id=None,
            ),
            LabelConfirmationView(
                first_event_id=open_b.first_event_id,
                segment_version=open_b.version or "",
                label_id=first_label.id,
            ),
        ],
        key=lambda view: view.segment_version,
    )
    assert (
        await create_label_confirmation_directory().find_by_first_events(
            session, uuid4(), first_event_ids=[closed.first_event_id]
        )
        == []
    )
