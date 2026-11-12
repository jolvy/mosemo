from collections import Counter
from datetime import UTC, date, datetime
from types import ModuleType
from uuid import UUID, uuid5

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.infrastructure.tables import devices
from mosemo.observation.bootstrap import create_observation_timeline
from mosemo.observation.domain.activities import ActivityRecord
from mosemo.observation.domain.timeline import ActivityTimelineKind, project_events
from mosemo.observation.infrastructure.tables import activity_records
from tests.support.seed_database import SeedDatabase

END_DATE = "2026-01-31"
NOW = datetime(2026, 2, 1, tzinfo=UTC)
RESULT_COLUMNS = (
    "suggested_label_id",
    "provider",
    "model",
    "prompt_version",
    "retrieved_example_ids",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "suggested_at",
)
SEGMENT_COLUMNS = (
    "segment_type",
    "started_at",
    "ended_at",
    "first_event_id",
    "last_event_id",
    "last_observed_at",
    "context",
    "reason",
)


def _one_user_args(seed_database: SeedDatabase) -> list[str]:
    return [
        *f"--users 1 --days 2 --end-date {END_DATE}".split(),
        *("--confirm-host", seed_database.host),
    ]


def _generated_counts(seed_load: ModuleType, *, seed: int, index: int) -> Counter:
    period = seed_load.SeedPeriod(seed=seed, end_date=date(2026, 1, 31), days=2)
    counts: Counter = Counter()
    for day in seed_load.generate_dummy_user(period, index):
        for table, rows in day.items():
            counts[table] += len(rows)
    return counts


def _assert_segments_match_projection(
    seed_database: SeedDatabase, account_id: UUID
) -> None:
    async def records(session: AsyncSession) -> list[ActivityRecord]:
        result = await session.scalars(
            select(ActivityRecord)
            .join(devices, devices.c.id == activity_records.c.device_id)
            .where(devices.c.account_id == account_id)
            .order_by(
                activity_records.c.observed_at,
                activity_records.c.device_id,
                activity_records.c.sequence,
                activity_records.c.event_id,
            )
        )
        return list(result)

    projected = [
        segment.to_segment(account_id=account_id, segment_id=None)
        for segment in project_events(seed_database.with_session(records))
    ]
    expected = [
        {
            "segment_type": segment.segment_type.value,
            **{column: getattr(segment, column) for column in SEGMENT_COLUMNS[1:]},
        }
        for segment in projected
    ]
    stored = seed_database.fetch(
        f"SELECT {', '.join(SEGMENT_COLUMNS)} FROM activity_timeline_segments"
        " WHERE account_id = :a ORDER BY started_at",
        a=account_id,
    )
    assert stored == expected


def _current_versions(
    seed_database: SeedDatabase, account_id: UUID, first_event_ids: set[UUID]
) -> dict[UUID, tuple[ActivityTimelineKind, str | None]]:
    timeline = create_observation_timeline()

    async def find(
        session: AsyncSession,
    ) -> dict[UUID, tuple[ActivityTimelineKind, str | None]]:
        found = {}
        for first_event_id in first_event_ids:
            segment = await timeline.find_by_first_event(
                session, account_id=account_id, first_event_id=first_event_id, now=NOW
            )
            assert segment is not None
            found[first_event_id] = (segment.kind, segment.version)
        return found

    return seed_database.with_session(find)


def _assert_labeling_matches_current_versions(
    seed_database: SeedDatabase, account_id: UUID
) -> None:
    confirmations = seed_database.fetch(
        "SELECT * FROM activity_label_confirmations WHERE account_id = :a",
        a=account_id,
    )
    proposals = seed_database.fetch(
        "SELECT * FROM activity_label_proposals WHERE account_id = :a", a=account_id
    )
    statuses = Counter(proposal["status"] for proposal in proposals)
    assert confirmations
    assert statuses["ready"] > 0
    assert statuses["superseded"] > 0
    current = [*confirmations, *(p for p in proposals if p["status"] != "superseded")]
    versions = _current_versions(
        seed_database,
        account_id,
        {row["first_event_id"] for row in [*confirmations, *proposals]},
    )
    for row in current:
        assert versions[row["first_event_id"]] == (
            ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY,
            row["segment_version"],
        )
    for confirmation in confirmations:
        assert confirmation["confirmed_at"] is not None
        assert confirmation["updated_at"] is not None
    for proposal in proposals:
        if proposal["status"] == "ready":
            for column in (
                "suggested_at",
                "retrieved_example_ids",
                "latency_ms",
                "input_tokens",
                "output_tokens",
            ):
                assert proposal[column] is not None, column
        elif proposal["status"] == "superseded":
            current_version = versions[proposal["first_event_id"]][1]
            assert proposal["segment_version"] != current_version
            assert all(proposal[column] is None for column in RESULT_COLUMNS)


def test_loaded_user_matches_projection_and_label_versions(
    seed_database: SeedDatabase, seed_load: ModuleType
) -> None:
    exit_code = seed_database.run(*_one_user_args(seed_database))

    assert exit_code == 0
    counts = seed_database.counts()
    assert counts["accounts"] == 1
    assert counts["devices"] == 1
    labels = seed_database.fetch("SELECT archived_at FROM labels")
    assert len(labels) == 6
    assert sum(label["archived_at"] is None for label in labels) == 5
    assert counts == {
        table: _generated_counts(seed_load, seed=1, index=1)[table]
        for table in seed_load.TABLE_ORDER
    }
    account_id = seed_database.dummy_account_id("load-seed-1-000001")
    _assert_segments_match_projection(seed_database, account_id)
    _assert_labeling_matches_current_versions(seed_database, account_id)
    receipts = seed_database.fetch("SELECT results FROM activity_batch_receipts")
    receipt_events = {
        UUID(result["eventId"]) for receipt in receipts for result in receipt["results"]
    }
    record_events = {
        row["event_id"]
        for row in seed_database.fetch("SELECT event_id FROM activity_records")
    }
    assert receipt_events == record_events


def test_more_users_load_only_new_users_and_other_seed_adds_its_own(
    seed_database: SeedDatabase, capsys: pytest.CaptureFixture[str]
) -> None:
    common = (
        "--days",
        "2",
        "--end-date",
        END_DATE,
        "--confirm-host",
        seed_database.host,
    )
    assert seed_database.run("--users", "1", *common) == 0
    first = seed_database.dummy_account_id("load-seed-1-000001")
    first_counts = seed_database.account_counts(first)
    capsys.readouterr()

    assert seed_database.run("--users", "2", *common) == 0

    output = capsys.readouterr().out
    assert "skipped dummy:1" in output
    assert "loaded dummy:2" in output
    lines = [line.split() for line in output.splitlines()]
    assert ["loaded", "users", "1"] in lines
    assert ["skipped", "users", "1"] in lines
    assert seed_database.account_counts(first) == first_counts
    second = seed_database.dummy_account_id("load-seed-1-000002")
    second_counts = seed_database.account_counts(second)
    assert second_counts["activity_records"] > 0

    assert seed_database.run("--seed", "2", "--users", "1", *common) == 0

    other = seed_database.dummy_account_id("load-seed-2-000001")
    assert seed_database.account_counts(other)["activity_records"] > 0
    assert seed_database.account_counts(first) == first_counts
    assert seed_database.account_counts(second) == second_counts


def test_failure_after_records_copy_rolls_back_the_whole_user(
    seed_database: SeedDatabase, seed_load: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _one_user_args(seed_database)
    copy_rows = seed_load.copy_rows

    async def fail_after_records(driver, *, table, rows):
        await copy_rows(driver, table=table, rows=rows)
        if table == "activity_records":
            raise OSError("injected failure after activity_records COPY")

    monkeypatch.setattr(seed_load, "copy_rows", fail_after_records)

    assert seed_database.run(*args) == 1
    assert set(seed_database.counts().values()) == {0}

    monkeypatch.setattr(seed_load, "copy_rows", copy_rows)

    assert seed_database.run(*args) == 0
    assert seed_database.counts() == {
        table: _generated_counts(seed_load, seed=1, index=1)[table]
        for table in seed_load.TABLE_ORDER
    }


def test_profiles_load_separately_and_skip_by_their_own_keys(
    seed_database: SeedDatabase,
    seed_load: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    measured = ("--profile", "measured", *_one_user_args(seed_database))

    assert seed_database.run(*measured) == 0

    (device,) = seed_database.fetch("SELECT idempotency_key FROM devices")
    assert device["idempotency_key"] == uuid5(
        seed_load.DEVICE_NAMESPACE, "1:measured:dummy:1"
    )
    measured_account = seed_database.dummy_account_id("load-seed-measured-1-000001")
    measured_counts = seed_database.account_counts(measured_account)
    assert "loaded measured:dummy:1" in capsys.readouterr().out

    assert seed_database.run("--profile", "heavy", *_one_user_args(seed_database)) == 0

    assert "loaded dummy:1" in capsys.readouterr().out
    assert seed_database.counts()["devices"] == 2
    seed_database.dummy_account_id("load-seed-1-000001")

    assert seed_database.run(*measured) == 0

    assert "skipped measured:dummy:1" in capsys.readouterr().out
    assert seed_database.counts()["devices"] == 2
    assert seed_database.account_counts(measured_account) == measured_counts
