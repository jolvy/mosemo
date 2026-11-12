import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from types import ModuleType
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from mosemo.observation.domain.activities import ActivityRecord, RecordType
from mosemo.observation.domain.timeline import project_events
from mosemo.observation.presentation.activities.mappers import activity_batch
from mosemo.observation.presentation.activities.schemas import ActivityBatchRequest
from mosemo.shared.domain.timezones import Timezone

SEOUL = ZoneInfo("Asia/Seoul")
END_DATE = date(2026, 1, 31)

type Days = list[dict[str, list[dict[str, Any]]]]


def _generate(seed_load: ModuleType, days: int) -> Days:
    period = seed_load.SeedPeriod(seed=1, end_date=END_DATE, days=days)
    return list(seed_load.generate_dummy_user(period, 1))


def _rows(days: Days, table: str) -> list[dict[str, Any]]:
    return [row for day in days for row in day.get(table, [])]


@pytest.fixture(scope="module")
def two_days(seed_load: ModuleType) -> Days:
    return _generate(seed_load, 2)


@pytest.fixture(scope="module")
def five_days(seed_load: ModuleType) -> Days:
    return _generate(seed_load, 5)


def _entity(row: dict[str, Any]) -> ActivityRecord:
    fields = {key: value for key, value in row.items() if key != "received_at"}
    fields["record_type"] = RecordType(row["record_type"])
    fields["timezone_id"] = Timezone(row["timezone_id"])
    return ActivityRecord(**fields)


def _request_record(row: dict[str, Any]) -> dict[str, Any]:
    record = {
        "focusSessionId": None
        if row["focus_session_id"] is None
        else str(row["focus_session_id"]),
        "eventId": str(row["event_id"]),
        "sequence": row["sequence"],
        "observedAt": row["observed_at"].isoformat().replace("+00:00", "Z"),
        "timezoneId": row["timezone_id"],
        "utcOffsetMinutes": row["utc_offset_minutes"],
        "recordType": row["record_type"],
    }
    return record | row["payload"]


SEGMENT_FIELDS = (
    "segment_type",
    "started_at",
    "ended_at",
    "first_event_id",
    "last_event_id",
    "last_observed_at",
    "context",
    "reason",
)


def test_generated_segments_match_domain_projection(two_days: Days) -> None:
    records = sorted(
        (_entity(row) for row in _rows(two_days, "activity_records")),
        key=lambda record: (record.observed_at, record.sequence),
    )
    segments = _rows(two_days, "activity_timeline_segments")
    account_id = segments[0]["account_id"]

    projected = [
        segment.to_segment(account_id=account_id, segment_id=None)
        for segment in project_events(records)
    ]

    assert [tuple(row[name] for name in SEGMENT_FIELDS) for row in segments] == [
        (
            segment.segment_type.value,
            segment.started_at,
            segment.ended_at,
            segment.first_event_id,
            segment.last_event_id,
            segment.last_observed_at,
            segment.context,
            segment.reason,
        )
        for segment in projected
    ]
    open_segments = [segment for segment in segments if segment["ended_at"] is None]
    assert open_segments == [segments[-1]]
    assert segments[-1]["segment_type"] == "activity"


def test_batches_pass_public_validation_and_match_receipts(two_days: Days) -> None:
    rows = {row["event_id"]: row for row in _rows(two_days, "activity_records")}
    receipts = _rows(two_days, "activity_batch_receipts")

    for receipt in receipts:
        batch_rows = [rows[UUID(result["eventId"])] for result in receipt["results"]]
        request = ActivityBatchRequest.model_validate(
            {
                "batchId": str(receipt["batch_id"]),
                "deviceId": str(receipt["device_id"]),
                "records": [_request_record(row) for row in batch_rows],
            }
        )

        assert 1 <= len(request.records) <= 200
        assert activity_batch(request).fingerprint == receipt["fingerprint"]
        assert {result["status"] for result in receipt["results"]} == {"stored"}
        assert {row["received_at"] for row in batch_rows} == {receipt["processed_at"]}
    assert sum(len(receipt["results"]) for receipt in receipts) == len(rows)


def test_sequence_starts_at_zero_and_increments_by_one(two_days: Days) -> None:
    sequences = [row["sequence"] for row in _rows(two_days, "activity_records")]

    assert sequences == list(range(len(sequences)))


def test_suspended_records_follow_the_daily_schedule(two_days: Days) -> None:
    suspended: dict[date, list[time]] = defaultdict(list)
    for row in _rows(two_days, "activity_records"):
        if row["payload"].get("state") == "suspended":
            local = row["observed_at"].astimezone(SEOUL)
            suspended[local.date()].append(local.time())
            assert row["focus_session_id"] is None

    assert suspended == {
        date(2026, 1, 30): [time(12), time(21)],
        date(2026, 1, 31): [time(12)],
    }


def test_focus_sessions_keep_completion_rules(two_days: Days) -> None:
    sessions = _rows(two_days, "focus_sessions")
    per_day = Counter(row["started_at"].astimezone(SEOUL).date() for row in sessions)

    assert per_day == {date(2026, 1, 30): 3, date(2026, 1, 31): 3}
    in_progress = [row for row in sessions if row["ended_at"] is None]
    assert [row["started_at"].astimezone(SEOUL) for row in in_progress] == [
        datetime(2026, 1, 31, 16, tzinfo=SEOUL)
    ]
    assert in_progress[0]["work_seconds"] is None
    assert in_progress[0]["label_id"] is None
    for row in sessions:
        if row["ended_at"] is None:
            continue
        duration = (row["ended_at"] - row["started_at"]).total_seconds()
        assert row["work_seconds"] <= duration
        assert (
            row["target_seconds"] == 0 or row["work_seconds"] <= row["target_seconds"]
        )
        assert row["label_id"] is not None
        assert row["description"] == ""


def test_only_observations_inside_focus_sessions_link_to_them(two_days: Days) -> None:
    sessions = _rows(two_days, "focus_sessions")

    for row in _rows(two_days, "activity_records"):
        expected = None
        if row["record_type"] == "activity_observation":
            expected = next(
                (
                    session["id"]
                    for session in sessions
                    if session["started_at"] <= row["observed_at"]
                    and (
                        session["ended_at"] is None
                        or row["observed_at"] <= session["ended_at"]
                    )
                ),
                None,
            )
        assert row["focus_session_id"] == expected


def _variant(context: dict[str, Any]) -> set[str]:
    variants = set()
    window = context["window"]
    web = context["web"]
    if window["status"] in {"absent", "unavailable"}:
        variants.add(f"window_{window['status']}")
    elif window["title"]["truncated"]:
        variants.add("truncated")
    if web["kind"] == "browser":
        if web["tabTitle"]["status"] == "redacted":
            variants.add("tab_redacted")
        if web["url"]["status"] == "redacted":
            variants.add("url_redacted")
    return variants


def test_five_days_cover_context_variants_and_proposal_states(
    five_days: Days,
) -> None:
    variants = set()
    for row in _rows(five_days, "activity_records"):
        context = row["payload"].get("context")
        if context is not None and context["kind"] == "detailed":
            variants |= _variant(context)
    statuses = Counter(
        row["status"] for row in _rows(five_days, "activity_label_proposals")
    )

    assert variants == {
        "window_absent",
        "window_unavailable",
        "tab_redacted",
        "url_redacted",
        "truncated",
    }
    assert set(statuses) == {"ready", "processing", "superseded"}


def test_archived_label_is_never_referenced(five_days: Days) -> None:
    (archived,) = [
        row["id"]
        for row in _rows(five_days, "labels")
        if row["archived_at"] is not None
    ]

    referenced = (
        {row["label_id"] for row in _rows(five_days, "focus_sessions")}
        | {row["label_id"] for row in _rows(five_days, "activity_label_confirmations")}
        | {
            row["suggested_label_id"]
            for row in _rows(five_days, "activity_label_proposals")
        }
    )
    assert archived not in referenced


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


def _assert_ready(row: dict[str, Any], *, examples: list[str]) -> None:
    assert row["lease_token"] is None
    assert row["lease_expires_at"] is None
    assert (row["provider"], row["model"], row["prompt_version"]) == ("seed",) * 3
    assert row["retrieved_example_ids"] == examples[-5:]
    assert 800 <= row["latency_ms"] <= 3000
    assert 1500 <= row["input_tokens"] <= 3000
    assert 20 <= row["output_tokens"] <= 60
    assert row["suggested_at"] == row["created_at"] == row["updated_at"]


def test_labeling_rows_follow_closed_detailed_segments(five_days: Days) -> None:
    segments = {
        row["first_event_id"]: row
        for row in _rows(five_days, "activity_timeline_segments")
        if row["segment_type"] == "activity"
    }
    confirmations = _rows(five_days, "activity_label_confirmations")
    confirmed_before = sorted(
        (row["confirmed_at"], str(row["id"])) for row in confirmations
    )

    for row in confirmations:
        segment = segments[row["first_event_id"]]
        assert segment["context"]["kind"] == "detailed"
        assert row["confirmed_at"] == segment["ended_at"] + timedelta(minutes=10)
        assert row["updated_at"] == row["confirmed_at"]
    for row in _rows(five_days, "activity_label_proposals"):
        segment = segments[row["first_event_id"]]
        assert segment["ended_at"] is not None
        assert row["attempt_count"] == 1
        assert row["next_attempt_at"] is None
        assert row["created_at"] == row["updated_at"]
        if row["status"] == "ready":
            earlier = [
                example_id
                for confirmed_at, example_id in confirmed_before
                if confirmed_at < row["suggested_at"]
            ]
            _assert_ready(row, examples=earlier)
            assert row["created_at"] == segment["ended_at"] + timedelta(seconds=30)
        elif row["status"] == "processing":
            assert row["lease_token"] is not None
            assert row["lease_expires_at"] == row["created_at"] + timedelta(minutes=2)
            assert row["created_at"] == segment["ended_at"] + timedelta(seconds=30)
            assert {row[name] for name in RESULT_COLUMNS} == {None}
        else:
            assert row["created_at"] == segment["ended_at"]
            assert row["lease_token"] is None
            assert row["lease_expires_at"] is None
            assert {row[name] for name in RESULT_COLUMNS} == {None}


@pytest.fixture(scope="module")
def measured_days(seed_load: ModuleType) -> Days:
    period = seed_load.SeedPeriod(seed=1, end_date=date(2026, 11, 7), days=14)
    return list(seed_load.generate_dummy_user(period, 1, profile="measured"))


def _closed_activity_segments(days: Days) -> list[dict[str, Any]]:
    return [
        row
        for row in _rows(days, "activity_timeline_segments")
        if row["segment_type"] == "activity" and row["ended_at"] is not None
    ]


def _activity_hours(days: Days) -> float:
    # The period's last segment stays open and has no length.
    return (
        sum(
            (row["ended_at"] - row["started_at"]).total_seconds()
            for row in _closed_activity_segments(days)
        )
        / 3600
    )


def _observations(days: Days) -> list[dict[str, Any]]:
    return [
        row
        for row in _rows(days, "activity_records")
        if row["record_type"] == "activity_observation"
    ]


def _consecutive_observations(
    days: Days,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pairs of activity observations with no collection state record between."""
    records = _rows(days, "activity_records")
    return [
        (previous, current)
        for previous, current in pairwise(records)
        if previous["record_type"] == current["record_type"] == "activity_observation"
    ]


def test_measured_activity_time_and_densities(measured_days: Days) -> None:
    hours = _activity_hours(measured_days)

    assert 100 <= hours <= 124
    assert 600 <= len(_observations(measured_days)) / hours <= 734
    assert 45 <= len(_closed_activity_segments(measured_days)) / hours <= 55


def test_measured_same_context_interval_median(measured_days: Days) -> None:
    intervals = sorted(
        (current["observed_at"] - previous["observed_at"]).total_seconds()
        for previous, current in _consecutive_observations(measured_days)
        if previous["payload"]["context"] == current["payload"]["context"]
    )

    assert 5.4 <= statistics.median(intervals) <= 5.6


def test_measured_silences_per_thousand_observations(measured_days: Days) -> None:
    silences = sum(
        current["observed_at"] - previous["observed_at"] > timedelta(seconds=60)
        for previous, current in _consecutive_observations(measured_days)
    )

    assert 7.7 <= silences * 1000 / len(_observations(measured_days)) <= 10.5


def test_measured_contexts_and_suspend_reasons(
    seed_load: ModuleType, measured_days: Days
) -> None:
    allowed = [
        {
            "kind": "detailed",
            "app": {
                "bundleId": {"status": "captured", "value": bundle_id},
                "name": {"status": "captured", "value": name},
            },
            "window": {"status": "unavailable", "reason": "accessibility_permission"},
            "web": {"kind": "not_applicable"},
        }
        for bundle_id, name, _ in seed_load.APPS
    ]
    contexts = [row["payload"]["context"] for row in _observations(measured_days)]
    reasons = {
        row["payload"]["reason"]
        for row in _rows(measured_days, "activity_records")
        if row["payload"].get("state") == "suspended"
    }

    assert [context for context in contexts if context["kind"] != "detailed"] == []
    assert [c for c in contexts if c["web"] != {"kind": "not_applicable"}] == []
    assert [
        c
        for c in contexts
        if c["window"]
        != {"status": "unavailable", "reason": "accessibility_permission"}
    ] == []
    assert reasons == {"screen_sleep", "system_sleep"}
    assert [context for context in contexts if context not in allowed] == []


def test_measured_segments_match_domain_projection(measured_days: Days) -> None:
    records = [_entity(row) for row in _rows(measured_days, "activity_records")]
    segments = _rows(measured_days, "activity_timeline_segments")
    account_id = segments[0]["account_id"]

    projected = [
        segment.to_segment(account_id=account_id, segment_id=None)
        for segment in project_events(records)
    ]

    assert [tuple(row[name] for name in SEGMENT_FIELDS) for row in segments] == [
        tuple(
            segment.segment_type.value
            if name == "segment_type"
            else getattr(segment, name)
            for name in SEGMENT_FIELDS
        )
        for segment in projected
    ]
