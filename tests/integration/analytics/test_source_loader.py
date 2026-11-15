"""`SourceSnapshotLoader.load_day` reads the segments `select_day_segments` picks."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.application.source_loader import (
    SourceSnapshotLoader,
    source_segment,
    source_session,
)
from mosemo.analytics.bootstrap import public_suffix_rules
from mosemo.analytics.domain.day_reading import select_day_segments
from mosemo.analytics.domain.source import DayWindow
from mosemo.analytics.domain.usage_targets import UsageTargetResolver
from mosemo.labeling.bootstrap import create_label_confirmation_directory
from mosemo.observation.bootstrap import (
    create_focus_session_log,
    create_observation_timeline,
)
from mosemo.shared.domain.timezones import Timezone
from tests.support.analytics_sources import SourceSeed, at, detailed

SEOUL = Timezone.ASIA_SEOUL
NOW = datetime(2026, 11, 10, tzinfo=UTC)
X = detailed("com.example.x")
Y = detailed("com.example.y")
DAYS = [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)]


async def seed_timeline(seed: SourceSeed) -> None:
    """Minutes from 11-03 00:00 Seoul (D):

    - D-1 23:14 Y, then a zero-second X at 23:19 sharing its instant with the
      first of 40 one-minute X segments, a zero-second X at 23:59 and X from
      23:59 crossing midnight to D 00:05 (the backward read needs two pages and
      the segments sharing an instant with the next one).
    - D 01:00 capture gap, X 01:10~02:00, X 23:30~23:50 then Y D+1 00:05~00:30
      inside a completed session D 23:30~D+1 00:40.
    - D+1 23:40~23:50 X followed by a zero-second X at D+2 00:00 (P7-B15 and a
      zero-second segment at the date start), then Y D+2 00:05~00:20.
    """
    seed.activity(-46, -41, Y)
    zero, first = sorted([uuid4(), uuid4()])
    seed.activity(-41, -41, X, segment_id=zero)
    for minute in range(40):
        seed.activity(
            -41 + minute,
            -40 + minute,
            X,
            segment_id=first if minute == 0 else None,
        )
    # The first date row shares its start with an earlier zero-second segment.
    zero, crossing = sorted([uuid4(), uuid4()])
    seed.activity(-1, -1, X, segment_id=zero)
    seed.activity(-1, 5, X, segment_id=crossing)
    seed.gap(60, 70)
    seed.activity(70, 120, X)
    seed.activity(1410, 1430, X)
    seed.activity(1445, 1470, Y)
    seed.focus(1410, 1480)
    seed.activity(2860, 2870, X)
    seed.activity(2880, 2880, X)
    seed.activity(2885, 2900, Y)


@pytest.mark.asyncio
@pytest.mark.parametrize("day", DAYS)
async def test_load_day_reads_the_segments_select_day_segments_chooses(
    integration_session: AsyncSession, day: date
) -> None:
    session = integration_session
    seed = SourceSeed(session)
    await seed.create_account()
    await seed_timeline(seed)
    other = SourceSeed(session)
    await other.create_account()
    await seed_timeline(other)
    await session.flush()
    timeline = create_observation_timeline()
    focus_sessions = create_focus_session_log()
    resolver = UsageTargetResolver(public_suffix_rules())
    loader = SourceSnapshotLoader(
        timeline=timeline,
        focus_sessions=focus_sessions,
        confirmations=create_label_confirmation_directory(),
        resolver=resolver,
    )
    early, late = at(-10 * 1440), at(10 * 1440)
    everything = [
        source_segment(row)
        for row in await timeline.read_range(
            session, seed.account_id, start=early, end=late, now=NOW
        )
    ]
    sessions = [
        source_session(view)
        for view in await focus_sessions.list_completed_started_between(
            session, seed.account_id, start=early, end=late
        )
    ]
    window = DayWindow.of(SEOUL, day)

    loaded = await loader.load_day(
        session, seed.account_id, timezone=SEOUL, window=window, now=NOW
    )

    expected = select_day_segments(
        everything, sessions=sessions, window=window, resolver=resolver
    )
    assert [segment.id for segment in loaded.snapshot.segments] == [
        segment.id for segment in expected
    ]
    assert loaded.snapshot.sessions == tuple(
        item for item in sessions if window.contains(item.started_at)
    )


@pytest.mark.asyncio
async def test_load_day_covers_the_tricky_reads(
    integration_session: AsyncSession,
) -> None:
    session = integration_session
    seed = SourceSeed(session)
    await seed.create_account()
    await seed_timeline(seed)
    await session.flush()
    loader = SourceSnapshotLoader(
        timeline=create_observation_timeline(),
        focus_sessions=create_focus_session_log(),
        confirmations=create_label_confirmation_directory(),
        resolver=UsageTargetResolver(public_suffix_rules()),
    )

    def starts(day: date) -> list[datetime]:
        return [segment.started_at for segment in loaded[day].snapshot.segments]

    loaded = {
        day: await loader.load_day(
            session,
            seed.account_id,
            timezone=SEOUL,
            window=DayWindow.of(SEOUL, day),
            now=NOW,
        )
        for day in DAYS
    }

    # The whole backward run, including the zero-second X at 23:19.
    assert starts(date(2026, 11, 3))[:2] == [at(-41), at(-41)]
    assert len(starts(date(2026, 11, 3))) == 41 + 2 + 4
    # The session window crosses midnight into D+1.
    assert at(1445) in starts(date(2026, 11, 3))
    # The zero-second segment at D+2 00:00 follows D+1 23:50 (P7-B15).
    assert starts(date(2026, 11, 4))[-1] == at(2880)
    assert starts(date(2026, 11, 5))[0] == at(2880)
