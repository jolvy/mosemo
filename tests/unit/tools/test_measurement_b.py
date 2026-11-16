"""측정 B 도구(`infra/measurement`)의 판정·결론·관찰 계획 순수 함수.

측정 결론(G1)과 판정 범위(C1·C6)는 measurement-b.json·md(Issue #312 결정 4·10)가 정한다. 도구는 pytest import
경로(`src`) 밖이라 파일 경로로 불러온다.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest

from mosemo.observation.domain.activities import MAX_OBSERVATION_GAP

MEASUREMENT = Path(__file__).resolve().parents[3] / "infra" / "measurement"
PLAN = json.loads((MEASUREMENT / "measurement-b.json").read_text())
CRIT = PLAN["criteria"]


def load(name: str) -> ModuleType:
    spec = spec_from_file_location(name, MEASUREMENT / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AM = load("analytics_measurement")
DENSE = load("dense_day_accounts")


def scenario(*, p95: float = 100, p99: float = 200, err: float = 0.0) -> dict:
    return {
        "p95_ms": p95,
        "p99_ms": p99,
        "arrival_err": err,
        "dropped_iterations": 0,
    }


def result(**scenarios: dict) -> dict[str, Any]:
    return {
        "scenarios": scenarios,
        "error_rate": 0.0,
        "reflect_s": {"p95": 1.0},
        "backlog": {"slope": {"per_s": 0.0}, "growing": False},
    }


def rounds(*passes: tuple[str, bool]) -> list[dict]:
    return [{"kind": kind, "pass": ok} for kind, ok in passes]


def test_plan_matches_issue_decisions() -> None:
    assert PLAN["order"] == ["R1", "R2", "R3", "R1", "R2", "R3"]
    assert PLAN["rounds"]["R3"]["heavy"] == pytest.approx(50 / 60)
    assert CRIT["c1_scenarios"] == ["read", "stats", "sessions"]
    assert CRIT["c6_scenarios"] == ["read", "write", "stats", "sessions"]


def test_k6_rate_is_what_k6_schedules() -> None:
    assert AM.k6_rate(0.13875630252100842) == pytest.approx(0.14)
    assert AM.k6_rate(0.001) == pytest.approx(0.01)


@pytest.mark.parametrize(
    ("passes", "verdict"),
    [
        (
            [("R1", True), ("R2", True), ("R3", True)] * 2,
            "no_split_now",
        ),
        (
            [("R1", True), ("R2", True), ("R3", False)]
            + [("R1", True), ("R2", True), ("R3", True)],
            "split_needed",
        ),
        (
            [("R1", True), ("R2", False), ("R3", False)]
            + [("R1", True), ("R2", True), ("R3", False)],
            "undecidable",
        ),
        ([("R1", True), ("R2", True), ("R3", True)], "undecidable"),
    ],
    ids=["r3-pass", "r3-fail-r2-pass", "r2-fail", "skipped-rounds"],
)
def test_g1_verdict(passes: list[tuple[str, bool]], verdict: str) -> None:
    assert AM.g1_verdict(rounds(*passes), PLAN) == verdict


def test_heavy_and_probe_do_not_decide_c1_or_c6() -> None:
    slow = scenario(p95=900, p99=1500, err=0.5)
    checks = AM.judge(
        result(
            read=scenario(),
            write=scenario(),
            stats=scenario(),
            sessions=scenario(),
            heavy=slow,
            probe=slow,
        ),
        CRIT,
    )
    assert checks["C1_read_latency"]
    assert checks["C6_arrival"]


@pytest.mark.parametrize("name", ["read", "stats", "sessions"])
def test_c1_applies_to_each_read_scenario(name: str) -> None:
    sc = {k: scenario() for k in ("read", "write", "stats", "sessions")}
    sc[name] = scenario(p95=301)
    assert not AM.judge(result(**sc), CRIT)["C1_read_latency"]


def test_c6_applies_to_write() -> None:
    sc = {k: scenario() for k in ("read", "stats", "sessions")}
    sc["write"] = scenario(err=0.03)
    assert not AM.judge(result(**sc), CRIT)["C6_arrival"]


@pytest.mark.parametrize(
    ("count", "bucket"),
    [(0, "small"), (999, "small"), (1000, "1000"), (5000, "5000"), (10000, "10000")],
)
def test_bucket_of(count: int, bucket: str) -> None:
    assert AM.bucket_of(count) == bucket


def test_bucket_of_other_counts_are_left_out() -> None:
    assert AM.bucket_of(1001) is None


def test_saved_from_output_reads_completed_line() -> None:
    lines = [
        json.dumps({"event": "other", "saved": 1}),
        json.dumps({"event": "batch job completed", "saved": 27}),
        "not json",
    ]
    assert AM.saved_from_output("\n".join(lines)) == "27"
    assert AM.saved_from_output("nothing") == "NA"


@pytest.mark.parametrize("count", [1000, 5000, 10000])
@pytest.mark.parametrize("hours", [0.5, 14, 22])
def test_observation_plan_keeps_segments_and_gap(count: int, hours: float) -> None:
    start = datetime(2026, 11, 20, tzinfo=UTC)
    plan = DENSE.observation_plan(count, (start, start + timedelta(hours=hours)))
    assert {i for _, i in plan} == set(range(count))
    assert [i for _, i in plan] == sorted(i for _, i in plan)
    assert DENSE.max_gap(plan) <= MAX_OBSERVATION_GAP
    assert all(b[0] > a[0] for a, b in pairwise(plan))


CONTEXTS = [
    {
        "kind": "detailed",
        "app": {
            "bundleId": {"status": "captured", "value": f"com.example.app{n}"},
            "name": {"status": "captured", "value": f"App {n}"},
        },
        "window": {"status": "unavailable", "reason": "accessibility_permission"},
        "web": {"kind": "not_applicable"},
    }
    for n in range(3)
]


@pytest.mark.parametrize("hours", [0.5, 20])
def test_dense_day_projects_exactly_count_segments_with_length(hours: float) -> None:
    start = datetime(2026, 11, 20, tzinfo=UTC)
    end = start + timedelta(hours=hours)
    plan = DENSE.observation_plan(1000, (start, end))
    records = DENSE.build_records(
        plan=plan, device_id=uuid4(), contexts=CONTEXTS, zone_name="Asia/Seoul"
    )
    rows = DENSE.segment_rows(
        records, account_id=uuid4(), now=end + DENSE.LAST_OBSERVATION_LEAD
    )
    assert len(rows) == 1000
    assert all(row[2] == "activity" for row in rows)
    started, ended = 3, 4
    assert all(
        row[ended] is not None and row[ended] > row[started] for row in rows[:-1]
    )
    assert rows[-1][ended] is not None
