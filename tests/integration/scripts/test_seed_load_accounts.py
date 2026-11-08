from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from tests.support.seed_database import SeedDatabase

AT = datetime(2025, 12, 1, tzinfo=UTC)


def _account(
    seed_database: SeedDatabase, *, active_labels: int = 5, archived_labels: int = 0
) -> UUID:
    account_id = uuid4()
    seed_database.execute(
        "INSERT INTO accounts (id, provider, provider_subject, timezone, created_at,"
        " last_authenticated_at) VALUES (:id, 'KAKAO', :subject, 'Asia/Seoul', :at, :at)",
        id=account_id,
        subject=str(account_id),
        at=AT,
    )
    for index in range(active_labels + archived_labels):
        seed_database.execute(
            "INSERT INTO labels (id, account_id, display_name, created_at, updated_at,"
            " archived_at) VALUES (:id, :account, :name, :at, :at, :archived)",
            id=uuid4(),
            account=account_id,
            name=f"label {index}",
            at=AT,
            archived=AT if index >= active_labels else None,
        )
    return account_id


def _device(seed_database: SeedDatabase, account_id: UUID) -> UUID:
    device_id = uuid4()
    seed_database.execute(
        "INSERT INTO devices (id, account_id, idempotency_key) VALUES (:id, :a, :key)",
        id=device_id,
        a=account_id,
        key=uuid4(),
    )
    return device_id


def _record(seed_database: SeedDatabase, device_id: UUID) -> UUID:
    event_id = uuid4()
    seed_database.execute(
        "INSERT INTO activity_records (event_id, device_id, sequence, record_type,"
        " observed_at, timezone_id, utc_offset_minutes, payload, received_at)"
        " VALUES (:id, :device, 0, 'collection_state_changed', :at, 'Asia/Seoul',"
        " 540, CAST(:payload AS jsonb), :at)",
        id=event_id,
        device=device_id,
        at=AT,
        payload='{"state": "active", "reason": "user_active"}',
    )
    return event_id


def _gap_segment(seed_database: SeedDatabase, account_id: UUID, event_id: UUID) -> None:
    seed_database.execute(
        "INSERT INTO activity_timeline_segments (id, account_id, segment_type,"
        " started_at, first_event_id, reason)"
        " VALUES (:id, :a, 'capture_gap', :at, :event, 'user_paused')",
        id=uuid4(),
        a=account_id,
        at=AT,
        event=event_id,
    )


def _run(seed_database: SeedDatabase, account_id: UUID, *, users: int = 1) -> int:
    return seed_database.run(
        *["--users", str(users), "--days", "2", "--end-date", "2026-01-31"],
        *("--account-id", str(account_id), "--confirm-host", seed_database.host),
    )


def test_account_without_activity_gets_a_seed_device_and_no_new_account_or_labels(
    seed_database: SeedDatabase, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = _account(seed_database)
    with_device = _account(seed_database)
    _device(seed_database, with_device)
    before = seed_database.counts()

    assert _run(seed_database, plain) == 0
    assert _run(seed_database, with_device) == 0

    after = seed_database.counts()
    assert after["accounts"] == before["accounts"]
    assert after["labels"] == before["labels"]
    assert after["devices"] == before["devices"] + 2
    for account_id in (plain, with_device):
        counts = seed_database.account_counts(account_id)
        assert counts["activity_records"] > 0
        assert counts["activity_timeline_segments"] > 0
        # Every record sits on the seed device, not on an existing one.
        assert seed_database.fetch(
            "SELECT DISTINCT d.idempotency_key FROM activity_records r"
            " JOIN devices d ON d.id = r.device_id WHERE d.account_id = :a",
            a=account_id,
        ) == [
            {
                "idempotency_key": seed_database.module.device_idempotency_key(
                    1, f"account:{account_id}"
                )
            }
        ]
    capsys.readouterr()

    assert _run(seed_database, plain) == 0

    output = capsys.readouterr().out
    assert f"skipped account:{plain}" in output
    assert ["skipped", "users", "1"] in [line.split() for line in output.splitlines()]
    assert seed_database.counts() == after


def test_account_counts_as_one_of_the_users_before_the_dummies(
    seed_database: SeedDatabase,
) -> None:
    account_id = _account(seed_database)

    assert _run(seed_database, account_id, users=3) == 0

    subjects = seed_database.fetch(
        "SELECT provider_subject FROM accounts WHERE id <> :a ORDER BY 1", a=account_id
    )
    assert subjects == [
        {"provider_subject": "load-seed-1-000001"},
        {"provider_subject": "load-seed-1-000002"},
    ]
    assert seed_database.account_counts(account_id)["activity_records"] > 0


def test_rejected_account_stops_before_any_dummy_is_written(
    seed_database: SeedDatabase,
) -> None:
    account_id = _account(seed_database)
    _record(seed_database, _device(seed_database, account_id))
    before = seed_database.counts()

    assert _run(seed_database, account_id, users=3) == 2

    assert seed_database.counts() == before


@pytest.mark.parametrize(
    "case", ["missing", "has_record", "has_segment", "no_active_label"]
)
def test_account_that_cannot_take_seed_data_is_rejected_without_writes(
    seed_database: SeedDatabase, case: str
) -> None:
    if case == "missing":
        account_id = uuid4()
    elif case == "has_record":
        account_id = _account(seed_database)
        _record(seed_database, _device(seed_database, account_id))
    elif case == "has_segment":
        # A segment with no record on the account's devices; its first event
        # belongs to another account so the foreign key holds.
        other = _account(seed_database)
        event_id = _record(seed_database, _device(seed_database, other))
        account_id = _account(seed_database)
        _gap_segment(seed_database, account_id, event_id)
    else:
        account_id = _account(seed_database, active_labels=0, archived_labels=2)
    before = seed_database.counts()

    assert _run(seed_database, account_id) == 2

    assert seed_database.counts() == before
