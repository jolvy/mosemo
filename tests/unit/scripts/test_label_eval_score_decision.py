"""AC-7: checks, decision and determinism of label_eval_score."""

import json
import re
from fractions import Fraction
from pathlib import Path
from random import Random
from types import ModuleType
from typing import Any

import pytest

from tests.support.label_eval_score import L1, L2, answers, case, result, write_jsonl

DECISION = """
primary = {primary}
min_accuracy_diff = 0.0
max_p95_latency_ms = 1000
max_usd_per_correct = 0.01
"""


def _run(
    module: ModuleType,
    out: Path,
    *,
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
    primary: str | None = None,
) -> tuple[int, Any]:
    argv = [
        "--cases",
        str(write_jsonl(out.with_suffix(".cases.jsonl"), cases)),
        "--results",
        str(write_jsonl(out.with_suffix(".results.jsonl"), results)),
        "--out",
        str(out),
    ]
    if primary is not None:
        decision = out.with_suffix(".decision.toml")
        decision.write_text(DECISION.format(primary=primary), encoding="utf-8")
        argv += ["--decision", str(decision)]
    code = module.main(argv)
    summary = out / "summary.json"
    return code, summary.read_text(encoding="utf-8") if summary.exists() else None


def _summary(module: ModuleType, out: Path, **kwargs: Any) -> Any:
    code, text = _run(module, out, **kwargs)
    assert code == 0
    return json.loads(text)


# Twenty activities: a gets the even ones right, b and c@* get all right.
CASES = [case(f"p/x{n}", L1) for n in range(20)]


def _rows(engine: str, *, right: bool | None = None, **values: Any) -> list[Any]:
    return [
        result(
            f"p/x{n}",
            engine,
            pred=L1 if (right if right is not None else n % 2 == 0) else L2,
            **values,
        )
        for n in range(20)
    ]


A = _rows("a")
B = _rows("b", right=True, confidence=0.9, cost_usd=0.001)


def test_oracle_is_exact_and_random_matches_one_over_labels_plus_one(
    label_eval_score: ModuleType,
) -> None:
    labels = ["l0", "l1", "l2", "l3"]
    choices = [*labels, None]
    cases = [case(f"p/x{n}", choices[n % 5]) for n in range(2000)]
    rows = [
        result(row["case_id"], "oracle", pred=row["gold"], confidence=1.0)
        for row in cases
    ]
    rows += [
        result(
            row["case_id"],
            "random",
            pred=Random(f"0/{row['case_id']}/0").choice(choices),
        )
        for row in cases
    ]

    summary = label_eval_score.score(
        answers(label_eval_score, cases, rows),
        prices={},
        seed=0,
        level=Fraction(19, 20),
    )
    exact = {e["engine"]: e["overall"]["exact_match"] for e in summary["engines"]}

    assert exact["oracle"] == 1.0
    assert abs(exact["random"] - 1 / (len(labels) + 1)) <= 0.05


def test_bare_c_resolves_to_the_only_route(
    label_eval_score: ModuleType, tmp_path: Path
) -> None:
    c_rows = _rows("c@ollama", right=True, confidence=0.9, cost_usd=0.001)

    summary = _summary(
        label_eval_score,
        tmp_path / "out",
        cases=CASES,
        results=A + B + c_rows,
        primary='["b", "c"]',
    )

    decision = summary["decision"]
    assert decision["primary"] == ["b", "c@ollama"]
    assert [v["label"] for v in decision["verdicts"]] == ["B", "C(ollama)"]
    assert summary["confidence_level"] == pytest.approx(0.975)


@pytest.mark.parametrize(
    ("results", "primary"),
    [
        # Two routes leave a bare c ambiguous.
        pytest.param(
            A + _rows("c@ollama") + _rows("c@openrouter"), '["c"]', id="two-routes"
        ),
        pytest.param(A + B, '["c"]', id="no-route"),
        pytest.param(B, '["b"]', id="no-baseline"),
        pytest.param(A, '["b"]', id="no-primary-rows"),
        pytest.param(A + _rows("b", batch_size=2), '["b"]', id="primary-only-batched"),
        pytest.param(A + _rows("b", rep=1), '["b"]', id="primary-only-rep-1"),
    ],
)
def test_decision_without_the_rows_it_compares_exits_2(
    label_eval_score: ModuleType,
    tmp_path: Path,
    results: list[Any],
    primary: str,
) -> None:
    code, summary = _run(
        label_eval_score,
        tmp_path / "out",
        cases=CASES,
        results=results,
        primary=primary,
    )

    assert code == 2
    assert summary is None


def test_without_baseline_rows_only_engine_metrics_are_written(
    label_eval_score: ModuleType, tmp_path: Path
) -> None:
    summary = _summary(label_eval_score, tmp_path / "out", cases=CASES, results=B)

    assert summary["comparisons"] == []
    assert summary["decision"] is None
    assert [e["engine"] for e in summary["engines"]] == ["b"]
    assert summary["engines"][0]["overall"]["exact_match"] == 1.0


def test_confidence_level_follows_the_number_of_primary_comparisons(
    label_eval_score: ModuleType, tmp_path: Path
) -> None:
    summary = _summary(
        label_eval_score,
        tmp_path / "out",
        cases=CASES,
        results=A + B,
        primary='["b"]',
    )
    expected = label_eval_score.score(
        answers(label_eval_score, CASES, A + B),
        prices={},
        seed=0,
        level=Fraction(19, 20),
    )["comparisons"]

    assert summary["confidence_level"] == pytest.approx(0.95)
    assert summary["comparisons"] == expected


def _comparison(engine: str, **values: Any) -> dict[str, Any]:
    return {
        "engine": engine,
        "batch_size": 1,
        "ci": {"lower": 0.05, "upper": 0.2},
        "usd_per_correct": 0.005,
        "p95_latency_ms": 500,
    } | values


def _decision(module: ModuleType, *primary: str) -> Any:
    return module.Decision(
        primary=primary,
        min_accuracy_diff=0.0,
        max_p95_latency_ms=1000,
        max_usd_per_correct=0.01,
    )


@pytest.mark.parametrize(
    ("values", "verdict"),
    [
        pytest.param({}, "adopt", id="adopt"),
        pytest.param({"ci": {"lower": -0.3, "upper": -0.1}}, "reject", id="ci-below"),
        pytest.param(
            {"ci": {"lower": -0.3, "upper": -0.1}, "usd_per_correct": None},
            "reject",
            id="ci-below-and-cost-null",
        ),
        pytest.param(
            {"p95_latency_ms": 2000, "usd_per_correct": None},
            "reject",
            id="slow-and-cost-null",
        ),
        pytest.param({"usd_per_correct": 0.02}, "reject", id="costly"),
        pytest.param(
            {"usd_per_correct": None}, "inconclusive", id="cost-null-blocks-adopt"
        ),
        pytest.param(
            {"ci": {"lower": -0.1, "upper": 0.2}}, "inconclusive", id="ci-straddles"
        ),
        pytest.param({"ci": None}, "inconclusive", id="no-ci"),
    ],
)
def test_each_primary_comparison_is_judged_in_contract_order(
    label_eval_score: ModuleType, values: dict[str, Any], verdict: str
) -> None:
    decision = label_eval_score.decide(
        [_comparison("b", **values)],
        _decision(label_eval_score, "b"),
        latency=None,
    )

    assert [v["verdict"] for v in decision["verdicts"]] == [verdict]


def test_no_correct_answers_leave_cost_per_correct_null(
    label_eval_score: ModuleType, tmp_path: Path
) -> None:
    wrong = _rows("b", right=False, confidence=0.9, cost_usd=0.001)

    summary = _summary(
        label_eval_score,
        tmp_path / "out",
        cases=CASES,
        results=A + wrong,
        primary='["b"]',
    )

    [comparison] = summary["comparisons"]
    assert comparison["usd_per_correct"] is None
    assert summary["decision"]["verdicts"][0]["usd_per_correct"] is None


def _latency(module: ModuleType, rows: list[dict[str, Any]]) -> list[Any]:
    return [module.parse_result(row) for row in rows]


def test_latency_results_replace_the_results_p95_even_when_missing(
    label_eval_score: ModuleType,
) -> None:
    comparisons = [_comparison("b", p95_latency_ms=500)]
    decision = _decision(label_eval_score, "b")
    slow = _latency(
        label_eval_score,
        [result(f"p/x{n}", "b", latency_ms=2000) for n in range(3)]
        + [result("p/x9", "b", batch_size=2, latency_ms=10)],
    )
    other_engine = _latency(label_eval_score, [result("p/x1", "a", latency_ms=10)])

    def judged(latency: list[Any] | None) -> tuple[Any, str]:
        [verdict] = label_eval_score.decide(comparisons, decision, latency=latency)[
            "verdicts"
        ]
        return verdict["p95_latency_ms"], verdict["verdict"]

    assert judged(None) == (500, "adopt")
    assert judged(slow) == (2000, "reject")
    assert judged(other_engine) == (None, "inconclusive")


@pytest.mark.parametrize(
    ("b", "c", "conclusion"),
    [
        pytest.param(
            {"ci": {"lower": -0.3, "upper": -0.1}},
            {"usd_per_correct": 0.5},
            "A 유지",
            id="keep-a",
        ),
        pytest.param({}, {"usd_per_correct": 0.5}, "B", id="b"),
        pytest.param(
            {"ci": {"lower": 0.01, "upper": 0.2}},
            {},
            "C(aimlapi)",
            id="c-higher-lower-bound",
        ),
        pytest.param({"usd_per_correct": 0.001}, {}, "B", id="tie-cheaper-b"),
        pytest.param(
            {"usd_per_correct": None},
            {"usd_per_correct": 0.5},
            "판단 불가",
            id="undecided",
        ),
    ],
)
def test_conclusion_follows_the_verdicts(
    label_eval_score: ModuleType,
    b: dict[str, Any],
    c: dict[str, Any],
    conclusion: str,
) -> None:
    decision = label_eval_score.decide(
        [_comparison("b", **b), _comparison("c@aimlapi", **c)],
        _decision(label_eval_score, "b", "c@aimlapi"),
        latency=None,
    )

    assert decision["conclusion"] == conclusion


def test_owner_cases_and_comparisons_outside_primary_are_not_judged(
    label_eval_score: ModuleType, tmp_path: Path
) -> None:
    b_none = _rows("b-none", right=False, confidence=0.9, cost_usd=0.001)
    persona_only = _summary(
        label_eval_score,
        tmp_path / "personas",
        cases=CASES,
        results=A + B + b_none,
        primary='["b"]',
    )
    owner_cases = [case(f"owner/x{n}", L1) for n in range(20)]
    owner_rows = [result(row["case_id"], "a", pred=L1) for row in owner_cases]
    owner_rows += [
        result(
            row["case_id"],
            "b",
            pred=L2,
            confidence=0.9,
            cost_usd=1.0,
            latency_ms=99_999,
        )
        for row in owner_cases
    ]
    with_owner = _summary(
        label_eval_score,
        tmp_path / "owner",
        cases=CASES + owner_cases,
        results=A + B + b_none + owner_rows,
        primary='["b"]',
    )

    assert [c["engine"] for c in with_owner["comparisons"]] == ["b", "b-none"]
    assert [v["engine"] for v in with_owner["decision"]["verdicts"]] == ["b"]
    assert with_owner["decision"] == persona_only["decision"]
    assert with_owner["comparisons"] == persona_only["comparisons"]
    assert with_owner["decision"]["conclusion"] == "B"
    owner = with_owner["engines"][1]["personas"]["owner"]
    assert owner["exact_match"] == 0.0


def test_same_inputs_give_the_same_summary_bytes(
    label_eval_score: ModuleType, tmp_path: Path
) -> None:
    inputs = {"cases": CASES, "results": A + B, "primary": '["b"]'}

    first = _run(label_eval_score, tmp_path / "first", **inputs)
    second = _run(label_eval_score, tmp_path / "second", **inputs)

    assert first[0] == 0
    assert first == second
    assert (tmp_path / "first" / "report.md").read_bytes() == (
        tmp_path / "second" / "report.md"
    ).read_bytes()


def _cli(
    module: ModuleType, tmp_path: Path, *, results: list[Any], extra: list[str]
) -> tuple[int, Any]:
    decision = tmp_path / "decision.toml"
    decision.write_text(DECISION.format(primary='["b"]'), encoding="utf-8")
    out = tmp_path / "out"
    code = module.main(
        [
            "--cases",
            str(write_jsonl(tmp_path / "cases.jsonl", CASES)),
            "--results",
            str(write_jsonl(tmp_path / "results.jsonl", results)),
            "--decision",
            str(decision),
            "--out",
            str(out),
            *extra,
        ]
    )
    summary = out / "summary.json"
    return code, json.loads(summary.read_text()) if summary.exists() else None


@pytest.mark.parametrize(
    ("latency_rows", "p95", "verdict"),
    [
        pytest.param(None, 100, "adopt", id="from-results"),
        # The last row of each key wins: 2000 ms rows were rerun at 10 ms.
        pytest.param(
            [
                result(f"p/x{n}", "b", latency_ms=2000, call_id=f"old{n}")
                for n in range(3)
            ]
            + [result(f"p/x{n}", "b", latency_ms=10) for n in range(3)],
            10,
            "adopt",
            id="last-rows",
        ),
        pytest.param(
            [result(f"p/x{n}", "b", latency_ms=2000) for n in range(3)],
            2000,
            "reject",
            id="slow-in-file",
        ),
        # No b calls in the file: no fallback to --results.
        pytest.param(
            [result("p/x1", "a", latency_ms=10)], None, "inconclusive", id="no-engine"
        ),
    ],
)
def test_latency_results_file_decides_p95_when_given(
    label_eval_score: ModuleType,
    tmp_path: Path,
    latency_rows: list[Any] | None,
    p95: int | None,
    verdict: str,
) -> None:
    extra = []
    if latency_rows is not None:
        path = write_jsonl(tmp_path / "latency.jsonl", latency_rows)
        extra = ["--latency-results", str(path)]

    code, summary = _cli(label_eval_score, tmp_path, results=A + B, extra=extra)

    assert code == 0
    decision = summary["decision"]
    source = "results" if latency_rows is None else "latency_results"
    assert decision["latency_source"] == source
    assert [(v["p95_latency_ms"], v["verdict"]) for v in decision["verdicts"]] == [
        (p95, verdict)
    ]


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"prob": float("nan")}, id="nan-prob"),
        pytest.param({"prob": 1.5}, id="prob-above-one"),
        pytest.param({"refused": "no"}, id="refused-text"),
        pytest.param({"latency_ms": None}, id="latency-null"),
        pytest.param({"batch_size": True}, id="batch-bool"),
        pytest.param({"cost_usd": -5.0}, id="negative-cost"),
        pytest.param({"confidence": -0.1}, id="negative-confidence"),
        pytest.param({"cost_usd": 10**400}, id="cost-overflows-float"),
        pytest.param({"input_tokens": 2**53}, id="tokens-too-large"),
    ],
)
def test_result_rows_outside_the_contract_exit_2(
    label_eval_score: ModuleType, tmp_path: Path, bad: dict[str, Any]
) -> None:
    rows = [*A, *B[:-1], result("p/x19", "b", pred=L1) | bad]

    assert _cli(label_eval_score, tmp_path, results=rows, extra=[]) == (2, None)
    assert not (tmp_path / "out").exists()


def test_confidence_above_one_is_accepted(
    label_eval_score: ModuleType, tmp_path: Path
) -> None:
    rows = [*A, *B[:-1], result("p/x19", "b", pred=L1, confidence=1.5)]

    code, summary = _cli(label_eval_score, tmp_path, results=rows, extra=[])

    assert code == 0
    assert summary["comparisons"][0]["other_answered"] == 20


@pytest.mark.parametrize(
    "line",
    [
        pytest.param('{"case_id": ' + "1" * 5000 + "}", id="too-many-digits"),
        # Not JSON whitespace, so not a blank line.
        pytest.param("\u2028", id="line-separator-only"),
        pytest.param("\u00a0", id="no-break-space-only"),
        pytest.param("[" * 100_000 + "]" * 100_000, id="deep-nesting"),
    ],
)
def test_unreadable_result_lines_exit_2_and_write_nothing(
    label_eval_score: ModuleType, tmp_path: Path, line: str
) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(line + "\n", encoding="utf-8")
    out = tmp_path / "out"
    cases = str(write_jsonl(tmp_path / "cases.jsonl", CASES))

    code = label_eval_score.main(
        ["--cases", cases, "--results", str(results), "--out", str(out)]
    )

    assert code == 2
    assert not out.exists()


def test_a_total_that_is_not_finite_exits_2_and_writes_nothing(
    label_eval_score: ModuleType, tmp_path: Path
) -> None:
    # Each cost is finite, but their total is not.
    rows = [*A, *_rows("b", right=True, confidence=0.9, cost_usd=1e308)]

    assert _cli(label_eval_score, tmp_path, results=rows, extra=[]) == (2, None)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "decision",
    [
        pytest.param(
            DECISION.format(primary='["b"]').replace("1000", "nan"), id="nan-limit"
        ),
        pytest.param(DECISION.format(primary='["b", "b"]'), id="duplicate"),
        pytest.param(DECISION.format(primary='["c", "c@ollama"]'), id="c-twice"),
        pytest.param(DECISION.format(primary="[]"), id="empty"),
        pytest.param("primary = ['b']\n", id="missing-keys"),
        pytest.param(
            DECISION.format(primary='["b"]').replace("0.0", "1" + "0" * 400),
            id="too-large",
        ),
        pytest.param(
            DECISION.format(primary='["b"]').replace("0.0", "1" + "0" * 5000),
            id="too-many-digits",
        ),
        pytest.param(
            DECISION.format(primary='["b"]')
            + "deep = "
            + "[" * 100_000
            + "]" * 100_000
            + "\n",
            id="deep-array",
        ),
        pytest.param(
            DECISION.format(primary='["b"]').replace(
                "= 0.01", "= " + "0x" + "F" * 4000
            ),
            id="hex-too-many-digits",
        ),
        pytest.param(
            DECISION.format(primary='["b"]').replace(
                "= 0.01", "= [" + "0x" + "F" * 4000 + "]"
            ),
            id="hex-in-array",
        ),
    ],
)
def test_bad_decision_file_exits_2(
    label_eval_score: ModuleType,
    tmp_path: Path,
    decision: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(decision, encoding="utf-8")
    c_rows = _rows("c@ollama", right=True, confidence=0.9, cost_usd=0.001)

    code, summary = _cli(
        label_eval_score,
        tmp_path,
        results=A + B + c_rows,
        extra=["--decision", str(path)],
    )

    assert (code, summary) == (2, None)
    err = capsys.readouterr().err
    assert err.startswith("rejected: ")
    assert not re.search(r"\d{50}", err)  # no repr of a long number


def test_line_separators_inside_activity_text_are_data(
    label_eval_score: ModuleType, tmp_path: Path
) -> None:
    cases = [case("p/x1", L1) | {"activity": "title: a\u2028b\u0085c"}]

    summary = _summary(
        label_eval_score,
        tmp_path / "out",
        cases=cases,
        results=[result("p/x1", "a\\|b\rc", pred=L1)],
    )

    assert summary["engines"][0]["overall"]["exact_match"] == 1.0
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "| a\\\\\\|b c | 1 |" in report


def test_crlf_files_with_blank_lines_are_read(
    label_eval_score: ModuleType, tmp_path: Path
) -> None:
    def crlf(path: Path, rows: list[dict[str, Any]]) -> str:
        text = "".join(json.dumps(row, ensure_ascii=False) + "\r\n" for row in rows)
        path.write_text(text + "\r\n", encoding="utf-8", newline="")
        return str(path)

    out = tmp_path / "out"
    code = label_eval_score.main(
        [
            "--cases",
            crlf(tmp_path / "cases.jsonl", CASES),
            "--results",
            crlf(tmp_path / "results.jsonl", B),
            "--out",
            str(out),
        ]
    )

    assert code == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["engines"][0]["overall"]["cases"] == 20
