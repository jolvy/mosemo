"""AC-6: metrics, same-coverage comparison and bootstrap of label_eval_score."""

import json
import math
from fractions import Fraction
from pathlib import Path
from random import Random
from types import ModuleType
from typing import Any

import pytest

from tests.support.label_eval_score import L1, L2, answers, case, result, write_jsonl

LEVEL = Fraction(19, 20)

# Two personas share x1 and x5; owner/x1 is reported apart from them.
CASES = [
    case("p/x1", L1, "personas_disagree"),
    case("q/x1", L2, "personas_disagree"),
    case("p/x2", None, "gold_null"),
    case("p/x3", L1, "cold_start"),
    case("p/x4", None, "gold_null"),
    case("p/x5", L1, "personas_disagree"),
    case("q/x5", None, "gold_null", "personas_disagree"),
    case("owner/x1", L1),
]
B_ROWS = [
    # Earlier rows of a key are replaced by its last row.
    result("p/x1", "b", error="HTTPStatusError", call_id="old-1"),
    result("p/x1", "b", pred=L1, confidence=0.9, prob=0.8),
    result("q/x1", "b", pred=L2, confidence=0.6, prob=0.6),
    result("p/x2", "b", pred=None, error="HTTPStatusError", call_id="old-2"),
    # An error is wrong although the gold label is null.
    result("p/x2", "b", error="TimeoutException"),
    # Wrong, and without prob: left out of ECE only.
    result("p/x3", "b", pred=L2, confidence=0.7, prob=None),
    result("p/x4", "b", refused=True),
    result("p/x5", "b", pred=L1, confidence=0.8, prob=0.9),
    result("q/x5", "b", pred=L1, confidence=0.5, prob=0.5),
    result("owner/x1", "b", pred=L2, confidence=0.9, prob=0.9),
]


def _score(module: ModuleType, results: list[dict[str, Any]], **prices: Any) -> Any:
    return module.score(
        answers(module, CASES, results), prices=prices, seed=0, level=LEVEL
    )


def _engine(summary: dict[str, Any], engine: str, batch_size: int = 1) -> Any:
    [entry] = [
        e
        for e in summary["engines"]
        if e["engine"] == engine and e["batch_size"] == batch_size
    ]
    return entry


def test_rate_metrics_use_the_last_row_and_count_errors_as_wrong(
    label_eval_score: ModuleType,
) -> None:
    overall = _engine(_score(label_eval_score, B_ROWS), "b")["overall"]

    assert overall["cases"] == 7
    assert overall["coverage"] == pytest.approx(5 / 7)
    assert overall["selective_accuracy"] == pytest.approx(3 / 5)
    assert overall["confident_wrong_rate"] == pytest.approx(2 / 7)
    assert overall["null_hit_rate"] == pytest.approx(1 / 3)
    assert overall["exact_match"] == pytest.approx(4 / 7)
    assert overall["error_rate"] == pytest.approx(1 / 7)
    assert overall["refusal_rate"] == pytest.approx(1 / 7)
    assert overall["persona_follow_rate"] == pytest.approx(1 / 2)


def test_ece_uses_prob_not_confidence_and_skips_null_prob(
    label_eval_score: ModuleType,
) -> None:
    overall = _engine(_score(label_eval_score, B_ROWS), "b")["overall"]

    # prob 0.8, 0.6, 0.9 right and 0.5 wrong, one per bin.
    assert overall["ece"] == pytest.approx((0.2 + 0.4 + 0.1 + 0.5) / 4)


def test_threshold_curve_counts_answers_at_or_above_each_confidence(
    label_eval_score: ModuleType,
) -> None:
    curve = _engine(_score(label_eval_score, B_ROWS), "b")["overall"]["threshold_curve"]

    assert [point["threshold"] for point in curve] == [i / 20 for i in range(21)]
    assert curve[0]["coverage"] == pytest.approx(5 / 7)
    assert curve[0]["selective_accuracy"] == pytest.approx(3 / 5)
    assert curve[13]["coverage"] == pytest.approx(3 / 7)
    assert curve[13]["selective_accuracy"] == pytest.approx(2 / 3)
    assert curve[20] == {"threshold": 1.0, "coverage": 0.0, "selective_accuracy": None}


def test_owner_personas_and_tags_are_broken_down_separately(
    label_eval_score: ModuleType,
) -> None:
    entry = _engine(_score(label_eval_score, B_ROWS), "b")

    assert {name: m["cases"] for name, m in entry["personas"].items()} == {
        "owner": 1,
        "p": 5,
        "q": 2,
    }
    assert entry["personas"]["owner"]["exact_match"] == 0.0
    tags = entry["tags"]
    assert tags["gold_null"]["cases"] == 3
    assert tags["gold_null"]["exact_match"] == pytest.approx(1 / 3)
    assert tags["cold_start"]["exact_match"] == 0.0
    assert tags["personas_disagree"]["persona_follow_rate"] == pytest.approx(1 / 2)
    assert tags["new_host"]["cases"] == 0
    assert tags["new_host"]["coverage"] is None


def test_oracle_confidence_and_engines_without_confidence_get_no_curve_or_ece(
    label_eval_score: ModuleType,
) -> None:
    rows = [
        result(row["case_id"], "oracle", pred=row["gold"], confidence=1.0, prob=None)
        for row in CASES
    ]
    rows += [result("p/x1", "a", pred=L1)]
    summary = _score(label_eval_score, rows)

    for engine in ("oracle", "a"):
        overall = _engine(summary, engine)["overall"]
        assert overall["threshold_curve"] is None
        assert overall["ece"] is None
    assert _engine(summary, "oracle")["overall"]["exact_match"] == 1.0


def test_latency_and_cost_count_each_batch_call_once(
    label_eval_score: ModuleType,
) -> None:
    engine = "c@openrouter"
    rows = [
        result(
            "p/x1",
            engine,
            batch_size=2,
            call_id="k1",
            pred=L1,
            latency_ms=300,
            cost_usd=0.004,
        ),
        result(
            "p/x3",
            engine,
            batch_size=2,
            call_id="k1",
            pred=L1,
            latency_ms=300,
            cost_usd=0.004,
        ),
        result(
            "p/x5",
            engine,
            batch_size=2,
            call_id="k2",
            pred=L1,
            latency_ms=500,
            cost_usd=0.006,
        ),
        result(
            "q/x5", engine, batch_size=2, call_id="k2", latency_ms=500, cost_usd=0.006
        ),
        # No cost in the response: priced from the table.
        result(
            "p/x2",
            engine,
            batch_size=2,
            call_id="k3",
            latency_ms=100,
            input_tokens=1000,
            output_tokens=200,
        ),
        result(
            "p/x4", engine, batch_size=2, call_id="k4", latency_ms=200, cost_usd=0.001
        ),
    ]
    price = label_eval_score.Price(
        input_usd_per_mtok=2.0, output_usd_per_mtok=10.0, usd_per_request=0.0005
    )

    overall = _engine(_score(label_eval_score, rows, **{engine: price}), engine, 2)[
        "overall"
    ]

    assert overall["latency_ms"] == {"p50": 200, "p95": 500, "p99": 500}
    assert overall["cost_usd"] == pytest.approx(0.004 + 0.006 + 0.0045 + 0.001)
    assert overall["usd_per_correct"] == pytest.approx(0.0155 / 6)


def test_cost_is_null_when_a_token_count_is_missing_for_a_token_price(
    label_eval_score: ModuleType,
) -> None:
    rows = [result("p/x1", "a", pred=L1, input_tokens=None, output_tokens=5)]
    rows += [result("p/x1", "random", pred=L1)]
    rows += [result("p/x1", "oracle", pred=L1, input_tokens=None)]
    price = label_eval_score.Price

    summary = _score(
        label_eval_score,
        rows,
        a=price(input_usd_per_mtok=0.1),
        oracle=price(usd_per_request=0.001),
    )

    assert _engine(summary, "a")["overall"]["cost_usd"] is None
    assert _engine(summary, "a")["overall"]["usd_per_correct"] is None
    # No price entry and no cost in the row.
    assert _engine(summary, "random")["overall"]["cost_usd"] is None
    # Token prices of 0 do not need token counts.
    assert _engine(summary, "oracle")["overall"]["cost_usd"] == pytest.approx(0.001)
    assert _engine(summary, "oracle")["price"]["usd_per_request"] == 0.001


def test_repeat_agreement_compares_preds_of_all_reps(
    label_eval_score: ModuleType,
) -> None:
    rows = [
        result("p/x1", "a", rep=0, pred=L1),
        result("p/x1", "a", rep=1, pred=L1),
        result("p/x3", "a", rep=0, pred=L1),
        result("p/x3", "a", rep=1, pred=None),
        result("p/x5", "a", rep=0, pred=L1),
    ]

    overall = _engine(_score(label_eval_score, rows), "a")["overall"]

    assert overall["cases"] == 3
    assert overall["repeat_agreement"] == pytest.approx(1 / 2)


COMPARE_CASES = [
    case("p/u1", L1),
    case("p/u2", L1),
    case("p/u3", L2),
    case("p/u4", None),
    case("p/u5", L1),
    case("p/u6", L1),
    case("p/u7", L1),
]
A_ROWS = [
    result("p/u1", "a", pred=L1),
    result("p/u2", "a", pred=L2),
    result("p/u3", "a"),
    result("p/u4", "a"),
    result("p/u5", "a", pred=L1),
    # Only the baseline has u6 and only b has u7.
    result("p/u6", "a", pred=L1),
]


def _b(case_id: str, pred: str | None, confidence: float) -> dict[str, Any]:
    return result(case_id, "b", pred=pred, confidence=confidence, cost_usd=0.003)


def _compare(module: ModuleType, rows: list[dict[str, Any]]) -> Any:
    summary = module.score(
        answers(module, COMPARE_CASES, A_ROWS + rows), prices={}, seed=0, level=LEVEL
    )
    [comparison] = summary["comparisons"]
    return comparison


def test_same_coverage_keeps_top_k_confident_answers_then_tests(
    label_eval_score: ModuleType,
) -> None:
    rows = [
        _b("p/u1", L1, 0.9),
        _b("p/u2", L1, 0.8),
        _b("p/u3", L2, 0.85),
        # Same confidence as u2; u2 wins on case_id. Dropped, so right (gold null).
        _b("p/u4", L1, 0.8),
        _b("p/u5", L2, 0.3),
        _b("p/u7", L1, 0.99),
    ]

    comparison = _compare(label_eval_score, rows)

    assert comparison["paired"] == 5
    assert comparison["unpaired"] == 2
    assert comparison["k"] == 3
    assert comparison["other_answered"] == 3
    assert comparison["coverage_short"] is False
    assert comparison["baseline_accuracy"] == pytest.approx(3 / 5)
    assert comparison["other_accuracy"] == pytest.approx(4 / 5)
    assert comparison["accuracy_diff"] == pytest.approx(1 / 5)
    assert comparison["mcnemar"] == {"b": 1, "c": 2, "p_value": 1.0}
    # 5 paired calls over the 3 cases b got right before the transform.
    assert comparison["usd_per_correct"] == pytest.approx(0.015 / 3)


def test_fewer_answers_than_k_are_kept_and_flagged(
    label_eval_score: ModuleType,
) -> None:
    rows = [_b("p/u1", L1, 0.9), _b("p/u3", L2, 0.2)]
    rows += [result(f"p/u{n}", "b", refused=True) for n in (2, 4, 5)]

    comparison = _compare(label_eval_score, rows)

    assert comparison["k"] == 3
    assert comparison["other_answered"] == 2
    assert comparison["coverage_short"] is True
    assert comparison["mcnemar"] == {"b": 1, "c": 1, "p_value": 1.0}


def test_engines_without_confidence_are_not_transformed(
    label_eval_score: ModuleType,
) -> None:
    rows = [result(f"p/u{n}", "random", pred=L1) for n in range(1, 6)]

    comparison = _compare(label_eval_score, rows)

    assert comparison["other_answered"] == 5
    assert comparison["coverage_short"] is False
    assert comparison["usd_per_correct"] is None


def test_no_baseline_rows_means_no_comparisons(label_eval_score: ModuleType) -> None:
    summary = _score(label_eval_score, B_ROWS)

    assert summary["comparisons"] == []
    assert _engine(summary, "b")["overall"]["cases"] == 7


@pytest.mark.parametrize(
    ("b", "c", "expected"),
    [(0, 5, 2 / 32), (1, 6, 2 * (1 + 7) / 128), (3, 3, 1.0), (0, 0, 1.0)],
)
def test_mcnemar_is_the_two_sided_exact_binomial_test(
    label_eval_score: ModuleType, b: int, c: int, expected: float
) -> None:
    assert label_eval_score.mcnemar_p(b, c) == pytest.approx(expected)


def _reference_ci(
    pairs: list[tuple[str, bool, bool]], *, seed: int, level: Fraction
) -> tuple[float, float]:
    clusters: dict[str, list[int]] = {}
    for activity_id, base, other in pairs:
        counts = clusters.setdefault(activity_id, [0, 0, 0])
        counts[0] += 1
        counts[1] += base
        counts[2] += other
    ordered = [clusters[key] for key in sorted(clusters)]
    random = Random(seed)
    diffs = []
    for _ in range(10_000):
        sample = random.choices(ordered, k=len(ordered))
        diffs.append(sum(s[2] - s[1] for s in sample) / sum(s[0] for s in sample))
    diffs.sort()
    tail = (1 - level) / 2
    return (
        diffs[math.ceil(tail * 10_000) - 1],
        diffs[math.ceil((1 - tail) * 10_000) - 1],
    )


PAIRS = [
    # x1 and x2 each hold two persona cases that are drawn together.
    ("x1", True, True),
    ("x1", False, True),
    ("x2", True, False),
    ("x2", False, False),
    ("x3", False, True),
    ("x4", True, True),
    ("x5", False, True),
]


@pytest.mark.parametrize("level", [Fraction(19, 20), Fraction(39, 40)])
def test_bootstrap_resamples_activities_with_the_seed(
    label_eval_score: ModuleType, level: Fraction
) -> None:
    ci = label_eval_score.bootstrap_ci(PAIRS, seed=7, level=level)

    assert ci == _reference_ci(PAIRS, seed=7, level=level)
    assert ci == label_eval_score.bootstrap_ci(PAIRS, seed=7, level=level)
    # The point estimate (5 - 3) / 7 lies inside.
    assert ci[0] < 2 / 7 < ci[1]


def test_bootstrap_of_a_constant_difference_is_that_difference(
    label_eval_score: ModuleType,
) -> None:
    pairs = [(f"x{n}", False, True) for n in range(4)]

    assert label_eval_score.bootstrap_ci(pairs, seed=0, level=LEVEL) == (1.0, 1.0)
    assert label_eval_score.bootstrap_ci([], seed=0, level=LEVEL) is None


def test_ece_weights_bins_by_answers_and_puts_prob_one_in_the_last_bin(
    label_eval_score: ModuleType,
) -> None:
    rows = [
        result("p/x1", "b", pred=L1, confidence=0.1, prob=1.0),
        result("p/x3", "b", pred=L2, confidence=0.1, prob=0.95),
        result("p/x5", "b", pred=L1, confidence=0.1, prob=0.32),
        result("q/x1", "b", pred=L2, confidence=0.1, prob=0.38),
    ]

    overall = _engine(_score(label_eval_score, rows), "b")["overall"]

    # Bin 9: 1/2 right, mean 0.975. Bin 3: 2/2 right, mean 0.35.
    assert overall["ece"] == pytest.approx((0.475 * 2 + 0.65 * 2) / 4)


def test_persona_follow_rate_counts_the_persona_cases_with_rows(
    label_eval_score: ModuleType,
) -> None:
    # q/x1 has no row (not sampled), so x1 is judged on p/x1 alone.
    rows = [
        result("p/x1", "a", pred=L1),
        result("p/x5", "a", pred=L1),
        result("q/x5", "a"),
    ]

    overall = _engine(_score(label_eval_score, rows), "a")["overall"]

    assert overall["persona_follow_rate"] == 1.0


def test_comparison_ci_resamples_the_persona_cases_of_an_activity_together(
    label_eval_score: ModuleType,
) -> None:
    cases = [
        case("p/y1", L1),
        case("q/y1", L2),
        case("p/y2", L1),
        case("q/y2", None),
        case("p/y3", L1),
    ]
    rows = [
        result("p/y1", "a", pred=L1),
        result("q/y1", "a", pred=L1),
        result("p/y2", "a", pred=L2),
        result("q/y2", "a"),
        result("p/y3", "a", pred=L1),
        _b("p/y1", L1, 0.9),
        _b("q/y1", L2, 0.8),
        _b("p/y2", L1, 0.7),
        _b("q/y2", L1, 0.6),
        # Fifth by confidence with k = 4: dropped, still wrong.
        _b("p/y3", L2, 0.5),
    ]

    [comparison] = label_eval_score.score(
        answers(label_eval_score, cases, rows), prices={}, seed=3, level=LEVEL
    )["comparisons"]

    pairs = [
        ("y1", True, True),
        ("y2", False, True),
        ("y3", True, False),
        ("y1", False, True),
        ("y2", True, False),
    ]
    lower, upper = label_eval_score.bootstrap_ci(pairs, seed=3, level=LEVEL)
    assert comparison["ci"] == {"lower": lower, "upper": upper}
    assert comparison["mcnemar"] == {"b": 2, "c": 2, "p_value": 1.0}


def _score_cli(
    module: ModuleType, tmp_path: Path, rows: list[dict[str, Any]], prices: str
) -> tuple[int, Any]:
    price_file = tmp_path / "prices.toml"
    price_file.write_text(prices, encoding="utf-8")
    out = tmp_path / "out"
    code = module.main(
        [
            "--cases",
            str(write_jsonl(tmp_path / "cases.jsonl", CASES)),
            "--results",
            str(write_jsonl(tmp_path / "results.jsonl", rows)),
            "--prices",
            str(price_file),
            "--out",
            str(out),
        ]
    )
    summary = out / "summary.json"
    return code, json.loads(summary.read_text()) if summary.exists() else None


PRICES = """
["c@openrouter"]
usd_per_request = 0.001
source_url = "https://example.com/pricing"
retrieved_on = 2026-11-16
"""
OPENROUTER_ROWS = [
    result("p/x1", "c@openrouter", pred=L1, output_tokens=None),
    result("p/x3", "c@openrouter", pred=L2),
]


def test_prices_file_fills_missing_keys_with_zero_by_engine_name(
    label_eval_score: ModuleType, tmp_path: Path
) -> None:
    code, summary = _score_cli(label_eval_score, tmp_path, OPENROUTER_ROWS, PRICES)

    assert code == 0
    entry = _engine(summary, "c@openrouter")
    assert entry["price"] == {
        "input_usd_per_mtok": 0.0,
        "output_usd_per_mtok": 0.0,
        "usd_per_request": 0.001,
        "source_url": "https://example.com/pricing",
        "retrieved_on": "2026-11-16",
    }
    assert entry["overall"]["cost_usd"] == pytest.approx(0.002)
    assert entry["overall"]["usd_per_correct"] == pytest.approx(0.002)


@pytest.mark.parametrize(
    "prices",
    [
        pytest.param('["c@openrouter"]\nusd_per_call = 1\n', id="unknown-key"),
        pytest.param('["c@openrouter"]\nusd_per_request = nan\n', id="not-finite"),
        pytest.param('["c@openrouter"]\nusd_per_request = "1"\n', id="not-number"),
        pytest.param('["c@openrouter"]\nsource_url = 3\n', id="url-not-text"),
        pytest.param('["c@openrouter"]\nusd_per_request = -1\n', id="negative"),
        pytest.param(
            '["c@openrouter"]\nusd_per_request = 1' + "0" * 400 + "\n", id="too-large"
        ),
        pytest.param(
            '["c@openrouter"]\nusd_per_request = '
            + "[" * 100_000
            + "]" * 100_000
            + "\n",
            id="deep-array",
        ),
        pytest.param(
            '["c@openrouter"]\nusd_per_request = ' + "0x" + "F" * 4000 + "\n",
            id="hex-too-many-digits",
        ),
        pytest.param(
            '["c@openrouter"]\nusd_per_request = [' + "0x" + "F" * 4000 + "]\n",
            id="hex-in-array",
        ),
    ],
)
def test_bad_prices_file_exits_2_and_writes_nothing(
    label_eval_score: ModuleType, tmp_path: Path, prices: str
) -> None:
    assert _score_cli(label_eval_score, tmp_path, OPENROUTER_ROWS, prices) == (2, None)
