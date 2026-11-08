from datetime import UTC, date, datetime, timedelta
from types import ModuleType
from uuid import UUID, uuid5

END_DATE = date(2026, 1, 31)


def _generate(seed_load: ModuleType, *, seed: int, days: int) -> list[dict]:
    period = seed_load.SeedPeriod(seed=seed, end_date=END_DATE, days=days)
    return list(seed_load.generate_dummy_user(period, 1))


def _rows(days: list[dict], table: str) -> list[dict]:
    return [row for day in days for row in day.get(table, [])]


def _milliseconds(value: datetime) -> int:
    return (value - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)


def test_same_seed_user_and_period_generate_identical_rows(
    seed_load: ModuleType,
) -> None:
    first = _generate(seed_load, seed=1, days=3)
    second = _generate(seed_load, seed=1, days=3)

    assert first == second
    assert set(first[0]) == {
        "accounts",
        "devices",
        "labels",
        "focus_sessions",
        "activity_records",
        "activity_batch_receipts",
        "activity_timeline_segments",
        "activity_label_proposals",
        "activity_label_confirmations",
    }


def test_other_seed_generates_other_event_ids_and_dummy_subject(
    seed_load: ModuleType,
) -> None:
    first = _generate(seed_load, seed=1, days=3)
    other = _generate(seed_load, seed=2, days=3)

    first_events = {row["event_id"] for row in _rows(first, "activity_records")}
    other_events = {row["event_id"] for row in _rows(other, "activity_records")}
    assert first_events.isdisjoint(other_events)
    assert _rows(first, "accounts")[0]["provider_subject"] == "load-seed-1-000001"
    assert _rows(other, "accounts")[0]["provider_subject"] == "load-seed-2-000001"


def test_time_ordered_ids_carry_their_time_in_milliseconds(
    seed_load: ModuleType,
) -> None:
    days = _generate(seed_load, seed=1, days=3)

    for record in _rows(days, "activity_records"):
        event_id: UUID = record["event_id"]
        assert event_id.version == 7
        assert event_id.variant == "specified in RFC 4122"
        assert event_id.int >> 80 == _milliseconds(record["observed_at"])
    for segment in _rows(days, "activity_timeline_segments"):
        assert segment["id"].version == 7
        assert segment["id"].int >> 80 == _milliseconds(segment["started_at"])


def test_dummy_account_labels_and_device_follow_the_contract(
    seed_load: ModuleType,
) -> None:
    days = _generate(seed_load, seed=1, days=3)
    start = datetime(2026, 1, 29, tzinfo=UTC)

    (account,) = _rows(days, "accounts")
    assert account["provider"] == "KAKAO"
    assert account["timezone"] == "Asia/Seoul"
    assert account["created_at"] == account["last_authenticated_at"] == start
    (device,) = _rows(days, "devices")
    assert device["idempotency_key"] == uuid5(seed_load.DEVICE_NAMESPACE, "1:dummy:1")
    labels = _rows(days, "labels")
    assert [label["display_name"] for label in labels] == [
        "코딩",
        "학습",
        "소통",
        "쇼핑",
        "여가",
        "보관된 라벨",
    ]
    assert [label["archived_at"] for label in labels] == [None] * 5 + [start]
    assert {label["created_at"] for label in labels} == {start}
    assert {label["updated_at"] for label in labels} == {start}


def test_account_user_uses_given_labels_without_account_or_label_rows(
    seed_load: ModuleType,
) -> None:
    account_id = UUID("01900000-0000-7000-8000-000000000001")
    label_ids = [UUID("01900000-0000-7000-8000-000000000002")]
    period = seed_load.SeedPeriod(seed=1, end_date=END_DATE, days=2)

    days = list(
        seed_load.generate_account_user(
            period, account_id=account_id, label_ids=label_ids
        )
    )

    assert _rows(days, "accounts") == []
    assert _rows(days, "labels") == []
    (device,) = _rows(days, "devices")
    assert device["account_id"] == account_id
    assert device["idempotency_key"] == uuid5(
        seed_load.DEVICE_NAMESPACE, f"1:account:{account_id}"
    )
    used = {
        row["label_id"] for row in _rows(days, "focus_sessions") if row["label_id"]
    } | {
        row["suggested_label_id"]
        for row in _rows(days, "activity_label_proposals")
        if row["suggested_label_id"]
    }
    assert used == set(label_ids)
