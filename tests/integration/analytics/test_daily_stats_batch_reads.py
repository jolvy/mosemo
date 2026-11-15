"""The batch with the product reads and source use cases (verification plan
「제품 조회·완료 API」 API-5~API-8, API-12) and the definition mark process.

Reads go through `GetDailyStats`, the use case behind the daily stats API.
"""

import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from mosemo.analytics.application import reads
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository
from mosemo.analytics.infrastructure.tables import analytics_daily_stats
from mosemo.identity.bootstrap import (
    create_account_directory,
    create_device_directory,
)
from mosemo.labeling.application.confirmations import LabelSelection
from mosemo.labeling.bootstrap import Labeling
from mosemo.labels.bootstrap import create_label_catalog
from mosemo.observation.application.activities import RecordActivity
from mosemo.observation.bootstrap import Observation, create_observation_timeline
from mosemo.observation.domain.activities import ActivityRecord, RecordType
from mosemo.observation.domain.focus_sessions import FocusSessionCompletion
from mosemo.observation.domain.timeline import segment_version
from mosemo.shared.domain.timezones import Timezone
from tests.support.analytics_batch import (
    DAY,
    EDITOR,
    NOTES,
    ROOT,
    BatchProcess,
    BatchWorld,
    FailingCalculator,
    definition,
)
from tests.support.analytics_sources import BASE, OPAQUE, SourceSeed, at

pytestmark = pytest.mark.asyncio

NEW_YORK = Timezone.AMERICA_NEW_YORK
DEFINITION_2 = definition(2)
MINUTE = timedelta(minutes=1)


def minutes(instant: datetime) -> float:
    """`instant` in the minutes `at` takes (from 11-03 00:00 Seoul)."""
    return (instant - BASE).total_seconds() / 60


def utc(month: int, day: int, hour: int, minute: int = 0) -> float:
    return minutes(datetime(2026, month, day, hour, minute, tzinfo=UTC))


async def old_definition_count(world: BatchWorld) -> int | None:
    async with world.engine.connect() as connection:
        return await connection.scalar(
            select(func.count()).where(analytics_daily_stats.c.definition_version < 2)
        )


async def test_api5_reads_before_and_after_the_batch_reflect_a_correction(
    world: BatchWorld,
) -> None:
    """API-5 (AC-8015): stored date → label correction → read before the batch
    → batch → read. Both reads are equal and reflect the correction, and so
    is the stored row."""
    seed = await world.account()
    async with world.seeding(seed):
        editor = seed.activity(540, 570, EDITOR)
        seed.activity(570, 580, NOTES)
    await world.mark_days(seed)
    await world.run()
    before = await world.stored(seed.account_id)
    assert before is not None

    world.clock.advance(MINUTE)
    labeling = Labeling(
        accounts=create_account_directory(),
        labels=create_label_catalog(),
        timeline=create_observation_timeline(),
        dispatcher=world.dispatcher,
        clock=world.clock,
    )
    async with world.factory() as session:
        assert editor.ended_at is not None
        await labeling.confirm_segment_label.execute(
            session,
            account_id=seed.account_id,
            selection=LabelSelection(
                segment_id=editor.id,
                segment_version=segment_version(editor, ended_at=editor.ended_at),
                label_id=seed.coding,
            ),
        )
    unbatched = await world.read(seed.account_id)
    await world.run()
    batched = await world.read(seed.account_id)

    assert unbatched.stats == batched.stats
    assert unbatched.stats != before.stats
    assert unbatched.stats.auxiliary.pending_us == 10 * 60_000_000
    stored = await world.stored(seed.account_id)
    assert stored is not None
    assert stored.stats == batched.stats
    assert await world.marker_rows(seed.account_id) == []


def observation(
    device_id, sequence: int, observed_at: datetime, context: dict
) -> ActivityRecord:
    return ActivityRecord(
        event_id=uuid4(),
        device_id=device_id,
        sequence=sequence,
        record_type=RecordType.ACTIVITY_OBSERVATION,
        observed_at=observed_at,
        timezone_id=Timezone.ASIA_SEOUL,
        utc_offset_minutes=540,
        payload={"context": context},
    )


async def test_api6_source_requests_succeed_while_the_batch_fails(
    world: BatchWorld,
) -> None:
    """API-6 (AC-8016): with the batch calculation failing, an activity batch, a
    label confirmation and a session completion succeed; reads of the past
    date reflect them; a working batch then stores the date."""
    seed = await world.account()
    async with world.seeding(seed):
        focus = seed.focus(780, None)
    await world.mark_days(seed)
    failing = world.refresh(calculator=FailingCalculator(fail_on=[DAY]))
    assert (await world.run(failing)).failures["compute"] == 1
    world.clock.advance(MINUTE)
    record = RecordActivity(
        devices=create_device_directory(),
        dispatcher=world.dispatcher,
        clock=world.clock,
    )
    timeline = create_observation_timeline()

    async with world.factory() as session:
        for sequence, minute, context in [
            (1, 540, EDITOR),
            (2, 540.5, EDITOR),
            (3, 541, OPAQUE),
        ]:
            await record.execute(
                session,
                account_id=seed.account_id,
                record=observation(seed.device_id, sequence, at(minute), context),
            )
        (editor,) = [
            segment
            for segment in await timeline.read_range(
                session, seed.account_id, start=at(540), end=at(541), now=world.clock()
            )
            if segment.context != OPAQUE
        ]
        await session.commit()
        assert editor.version is not None
        await Labeling(
            accounts=create_account_directory(),
            labels=create_label_catalog(),
            timeline=timeline,
            dispatcher=world.dispatcher,
            clock=world.clock,
        ).confirm_segment_label.execute(
            session,
            account_id=seed.account_id,
            selection=LabelSelection(
                segment_id=editor.id,
                segment_version=editor.version,
                label_id=seed.coding,
            ),
        )
        await Observation(
            accounts=create_account_directory(),
            devices=create_device_directory(),
            labels=create_label_catalog(),
            dispatcher=world.dispatcher,
            clock=world.clock,
        ).complete_focus_session.execute(
            session,
            account_id=seed.account_id,
            focus_session_id=focus.id,
            completion=FocusSessionCompletion(
                ended_at=at(800), work_seconds=60, label_id=seed.coding, description=""
            ),
        )

    view = await world.read(seed.account_id)
    assert view.stats == await world.computed(seed.account_id)
    assert view.stats.sessions.count == 1
    assert view.stats.usage.observed_us > 0
    assert view.stats.auxiliary.pending_us < view.stats.usage.observed_us
    assert await world.stored(seed.account_id) is None
    await world.make_due()
    assert (await world.run()).saved == 1
    stored = await world.stored(seed.account_id)
    assert stored is not None
    assert stored.stats == view.stats
    assert await world.marker_rows(seed.account_id) == []


async def test_api7_stored_rows_equal_the_read_computation_on_boundary_dates(
    world: BatchWorld,
) -> None:
    """API-7 (AC-8022): DST dates (23 and 25 hours), a zero-second segment at
    midnight, a stay over midnight and a session over midnight, in New York.
    The stored C1·C2·C7, session sums and auxiliary values equal the same
    date computed at read time."""
    seed = await world.account()
    async with world.factory() as session:
        await session.execute(
            text("UPDATE accounts SET timezone = :tz WHERE id = :id"),
            {"tz": NEW_YORK.value, "id": seed.account_id},
        )
        await session.commit()
    async with world.seeding(seed):
        # 2026-03-08 (23 hours): 01:30 EST → 04:00 EDT.
        seed.activity(utc(3, 8, 6, 30), utc(3, 8, 8), EDITOR)
        # 2026-11-01 (25 hours): 00:30 EDT → 01:30 EST across the repeated hour.
        seed.activity(utc(11, 1, 4, 30), utc(11, 1, 6, 30), EDITOR)
        seed.activity(utc(11, 1, 6, 30), utc(11, 1, 7), NOTES)
        # 11-02 23:30 → 11-03 00:40 EST session; a zero-second segment at 11-03 0:00.
        seed.activity(utc(11, 3, 4, 20), utc(11, 3, 5), EDITOR)
        seed.activity(utc(11, 3, 5), utc(11, 3, 5), NOTES)
        seed.activity(utc(11, 3, 5), utc(11, 3, 5, 50), EDITOR)
        seed.focus(utc(11, 3, 4, 30), utc(11, 3, 5, 40), label_id=seed.coding)
        # A stay over 11-04 0:00 EST: the same app on both sides of midnight.
        seed.activity(utc(11, 4, 4, 40), utc(11, 4, 5, 10), NOTES)
        seed.activity(utc(11, 4, 5, 10), utc(11, 4, 5, 30), NOTES)
    days = [date(2026, 3, 8), *(date(2026, 11, day) for day in (1, 2, 3, 4))]
    await world.mark_days(seed, *days, timezone=NEW_YORK)

    report = await world.run()

    assert report.saved == len(days)
    for day in days:
        stored = await world.stored(seed.account_id, day)
        assert stored is not None
        assert stored.timezone == NEW_YORK
        computed = await world.computed(seed.account_id, day, timezone=NEW_YORK)
        assert stored.stats == computed, day
        view = await world.read(seed.account_id, day)
        assert view.stats == computed, day
    # The session counts on the date it started (11-02) only.
    counts = []
    for day in (date(2026, 11, 2), date(2026, 11, 3)):
        stored = await world.stored(seed.account_id, day)
        assert stored is not None
        counts.append(stored.stats.sessions.count)
    assert counts == [1, 0]


async def test_api8_reads_use_the_new_definition_after_it_rises(
    world: BatchWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API-8 (AC-8023): a date stored with definition 1; the definition row and
    code go to 2. Reads compute with definition 2; after the marks and the
    batch the stored row is definition 2."""
    seed = await world.account()
    async with world.seeding(seed):
        seed.activity(540, 570, EDITOR)
    await world.mark_days(seed)
    await world.run()
    await world.set_definition_row(2)
    monkeypatch.setattr(reads, "CURRENT_DEFINITION", DEFINITION_2)

    unprocessed = await world.read(seed.account_id)
    assert unprocessed.definition_version == 2
    assert unprocessed.stats == await world.computed(seed.account_id)

    assert (await world.mark(definition=DEFINITION_2)).markers == 1
    assert (await world.run(world.refresh(definition=DEFINITION_2))).saved == 1
    stored = await world.stored(seed.account_id)
    assert stored is not None
    assert stored.definition_version == 2
    processed = await world.read(seed.account_id)
    assert (processed.definition_version, processed.stats) == (2, stored.stats)


class InterruptedMarks(RecomputeMarkerRepository):
    """Fails when the marks reach a second account (the mark is interrupted)."""

    def __init__(self) -> None:
        self.accounts: set[object] = set()

    async def upsert_definition(self, db_session, *, account_id, **values) -> None:
        self.accounts.add(account_id)
        if len(self.accounts) > 1:
            raise ConnectionResetError("interrupted")
        await super().upsert_definition(db_session, account_id=account_id, **values)


async def test_api12_interrupted_and_rerun_marks_converge_to_the_new_definition(
    world: BatchWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API-12 (AC-8027): definition up → marks stop after one account → rerun →
    batches. No old definition row or marker is left, and every read during
    the process is definition 2."""
    seeds: list[SourceSeed] = []
    for _ in range(3):
        seed = await world.account()
        async with world.seeding(seed):
            seed.activity(540, 570, EDITOR)
        await world.mark_days(seed, DAY, date(2026, 11, 4))
        seeds.append(seed)
    await world.run()
    assert await old_definition_count(world) == 6
    await world.set_definition_row(2)
    monkeypatch.setattr(reads, "CURRENT_DEFINITION", DEFINITION_2)

    async def assert_reads_are_definition_2() -> None:
        for seed in seeds:
            view = await world.read(seed.account_id)
            assert view.definition_version == 2
            assert view.stats == await world.computed(seed.account_id)

    with pytest.raises(ConnectionResetError):
        await world.mark(definition=DEFINITION_2, markers=InterruptedMarks())
    assert len(await world.marker_rows()) == 2
    await assert_reads_are_definition_2()

    rerun = await world.mark(definition=DEFINITION_2)
    assert (rerun.accounts, rerun.markers) == (3, 6)
    await assert_reads_are_definition_2()
    while await world.marker_rows():
        report = await world.run(world.refresh(definition=DEFINITION_2))
        assert report.failures.total() == 0
        await assert_reads_are_definition_2()

    assert await old_definition_count(world) == 0
    assert (await world.mark(definition=DEFINITION_2)).markers == 0


async def test_definition_mark_process_exits_zero_with_the_test_settings(
    world: BatchWorld, tmp_path
) -> None:
    """`python -m mosemo.entrypoints.batch run analytics-definition-mark` with
    the CI settings (MOSEMO_ENV=test, LABEL_MODEL, DB_*)."""
    seed = await world.account()
    async with world.seeding(seed):
        seed.activity(540, 570, EDITOR)
    await world.save_definition_row(seed, definition_version=1, source_version=1)
    environment = BatchProcess(world.url, tmp_path).environment()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mosemo.entrypoints.batch",
            "run",
            "analytics-definition-mark",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    (completed,) = [line for line in lines if line["event"] == "batch job completed"]
    assert completed["operation"] == "analytics-definition-mark"
    assert (completed["accounts"], completed["markers"]) == (0, 0)
    assert await world.marker_rows() == []
