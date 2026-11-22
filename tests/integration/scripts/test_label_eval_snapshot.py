import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

from mosemo.labeling.application.proposals import summarize_context
from tests.support.seed_database import SeedDatabase

KEYS = ("app_bundle_id", "app_name", "window_title", "tab_title", "web_host")
START = "2026-01-31T03:00:00+00:00"
END = "2026-01-31T09:00:00+00:00"


def _load_two_users(seed_database: SeedDatabase) -> tuple[UUID, UUID]:
    args = ["--users", "2", "--days", "1", "--end-date", "2026-01-31"]
    assert seed_database.run(*args, "--confirm-host", seed_database.host) == 0
    return (
        seed_database.dummy_account_id("load-seed-1-000001"),
        seed_database.dummy_account_id("load-seed-1-000002"),
    )


def _snapshot(
    module: ModuleType,
    out: Path,
    account_id: UUID,
    *,
    start: str,
    end: str,
    as_of: str,
) -> list[dict[str, Any]]:
    args = ["snapshot", "--account-id", str(account_id), "--from", start]
    exit_code = module.main([*args, "--to", end, "--as-of", as_of, "--out", str(out)])
    assert exit_code == 0
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]


def _segments(seed_database: SeedDatabase, account_id: UUID) -> list[dict[str, Any]]:
    return seed_database.fetch(
        "SELECT first_event_id, segment_type, ended_at, last_observed_at, context"
        " FROM activity_timeline_segments WHERE account_id = :a",
        a=account_id,
    )


def _expected(segments: list[dict[str, Any]], *, start: str, end: str) -> list[UUID]:
    """Stored closed detailed segments ending in [start, end), in snapshot order."""
    low, high = datetime.fromisoformat(start), datetime.fromisoformat(end)
    closed = [
        segment
        for segment in segments
        if segment["segment_type"] == "activity"
        and segment["context"]["kind"] == "detailed"
        and segment["ended_at"] is not None
        and low <= segment["ended_at"] < high
    ]
    closed.sort(key=lambda segment: (segment["ended_at"], segment["first_event_id"]))
    return [segment["first_event_id"] for segment in closed]


def test_snapshot_holds_the_accounts_closed_detailed_segments_in_range(
    seed_database: SeedDatabase, label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    account_id, other_id = _load_two_users(seed_database)
    segments = _segments(seed_database, account_id)
    (open_segment,) = [s for s in segments if s["ended_at"] is None and s["context"]]
    as_of = open_segment["last_observed_at"].isoformat()

    rows = _snapshot(
        label_eval_cases,
        tmp_path / "activities.jsonl",
        account_id,
        start=START,
        end=END,
        as_of=as_of,
    )

    assert [UUID(row["activity_id"]) for row in rows] == _expected(
        segments, start=START, end=END
    )
    ended = [s["ended_at"] for s in segments if s["ended_at"] is not None]
    assert min(ended) < datetime.fromisoformat(START)
    assert max(ended) >= datetime.fromisoformat(END)
    assert any(s["context"] and s["context"]["kind"] == "opaque" for s in segments)
    other_ids = {s["first_event_id"] for s in _segments(seed_database, other_id)}
    assert other_ids.isdisjoint(UUID(row["activity_id"]) for row in rows)
    contexts = {s["first_event_id"]: s["context"] for s in segments}
    for row in rows:
        assert row["account_id"] == str(account_id)
        assert row["version"]
        assert row["ended_at"].endswith("Z")
        assert row["activity"] == summarize_context(contexts[UUID(row["activity_id"])])
        fields = row["fields"]
        assert list(fields) == sorted(KEYS)
        assert row["activity"] == "\n".join(
            f"{key}: {fields[key]}" for key in KEYS if fields[key] is not None
        )
    assert any(row["fields"]["web_host"] for row in rows)
    assert any(row["fields"]["window_title"] is None for row in rows)


def test_segment_in_progress_at_as_of_is_left_out_and_reruns_match(
    seed_database: SeedDatabase, label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    account_id, _ = _load_two_users(seed_database)
    segments = _segments(seed_database, account_id)
    (open_segment,) = [s for s in segments if s["ended_at"] is None and s["context"]]
    as_of = open_segment["last_observed_at"].isoformat()
    outputs = [tmp_path / "first.jsonl", tmp_path / "second.jsonl"]

    runs = [
        _snapshot(
            label_eval_cases, out, account_id, start=START, end=as_of, as_of=as_of
        )
        for out in outputs
    ]

    ids = [UUID(row["activity_id"]) for row in runs[0]]
    assert open_segment["first_event_id"] not in ids
    assert ids == _expected(segments, start=START, end=as_of)
    first, second = (out.read_bytes() for out in outputs)
    assert first == second
    digest = hashlib.sha256(first).hexdigest()
    assert (tmp_path / "first.jsonl.sha256").read_text(encoding="utf-8") == (
        f"{digest}  first.jsonl\n"
    )


def test_to_after_as_of_is_rejected_without_output(
    seed_database: SeedDatabase, label_eval_cases: ModuleType, tmp_path: Path
) -> None:
    out = tmp_path / "activities.jsonl"

    exit_code = label_eval_cases.main(
        [
            *("snapshot", "--account-id", str(UUID(int=1)), "--from", START),
            *("--to", END, "--as-of", START, "--out", str(out)),
        ]
    )

    assert exit_code == 2
    assert not out.exists()
