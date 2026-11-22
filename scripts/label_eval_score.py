"""Score label suggestion engine results and decide (Plan #309, T3).

Reads the cases from ``label_eval_cases.py cases`` and the result rows the run
script appends, then writes ``summary.json`` and ``report.md``: metrics per
engine and batch size, the same-coverage comparisons against ``(a, batch 1,
rep 0)`` and, with ``--decision``, the verdicts and the conclusion fixed in
advance. The same inputs give the same bytes. Statistics use the standard
library only.

Usage: ``python scripts/label_eval_score.py --help``.
"""

import argparse
import json
import math
import sys
import tomllib
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from fractions import Fraction
from pathlib import Path
from random import Random
from typing import Any

type Row = dict[str, Any]

EXIT_USAGE = 2
OWNER = "owner"
BASELINE_ENGINE = "a"
CONFIDENCE_ENGINES = frozenset({"b", "b-none"})
CONFIDENCE_PREFIXES = ("c@", "c-none@")
C_PREFIX = "c@"
TAGS = ("gold_null", "cold_start", "new_host", "personas_disagree")
THRESHOLD_STEPS = 20
ECE_BINS = 10
BOOTSTRAP_ROUNDS = 10_000
FAMILY_ALPHA = Fraction(5, 100)
DEFAULT_LEVEL = 1 - FAMILY_ALPHA
PERCENTILES = (50, 95, 99)
TOKENS_PER_PRICE_UNIT = 1_000_000
PRICE_KEYS = frozenset(
    {
        "input_usd_per_mtok",
        "output_usd_per_mtok",
        "usd_per_request",
        "source_url",
        "retrieved_on",
    }
)
DECISION_KEYS = frozenset(
    {"primary", "min_accuracy_diff", "max_p95_latency_ms", "max_usd_per_correct"}
)
ADOPT = "adopt"
REJECT = "reject"
INCONCLUSIVE = "inconclusive"
KEEP_A = "A 유지"
UNDECIDED = "판단 불가"


class InputError(Exception):
    """An argument or input file the command rejects; nothing is written."""


@dataclass(frozen=True)
class Case:
    case_id: str
    persona: str
    activity_id: str
    gold: str | None
    tags: frozenset[str]


@dataclass(frozen=True)
class Result:
    case_id: str
    engine: str
    batch_size: int
    rep: int
    call_id: str
    pred: str | None
    confidence: float | None
    prob: float | None
    refused: bool
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    error: str | None

    @property
    def answered(self) -> bool:
        """An error-free non-null answer."""
        return self.error is None and self.pred is not None


TEXT = "text"
COUNT = "count"
NUMBER = "number"
UNIT = "unit"
FLAG = "flag"
MAX_COUNT = 2**53
JSON_WHITESPACE = " \t\r"
# Kind of each result field and whether it may be null.
RESULT_KINDS = {
    "case_id": (TEXT, False),
    "engine": (TEXT, False),
    "batch_size": (COUNT, False),
    "rep": (COUNT, False),
    "call_id": (TEXT, False),
    "pred": (TEXT, True),
    "confidence": (NUMBER, True),
    "prob": (UNIT, True),
    "refused": (FLAG, False),
    "latency_ms": (COUNT, False),
    "input_tokens": (COUNT, True),
    "output_tokens": (COUNT, True),
    "cost_usd": (NUMBER, True),
    "error": (TEXT, True),
}


@dataclass(frozen=True)
class Answer:
    case: Case
    result: Result

    @property
    def correct(self) -> bool:
        # An error is wrong even when the gold label is null.
        return self.result.error is None and self.result.pred == self.case.gold


@dataclass(frozen=True)
class Price:
    input_usd_per_mtok: float = 0.0
    output_usd_per_mtok: float = 0.0
    usd_per_request: float = 0.0
    source_url: str | None = None
    retrieved_on: str | None = None


@dataclass(frozen=True)
class Decision:
    primary: tuple[str, ...]
    min_accuracy_diff: float
    max_p95_latency_ms: float
    max_usd_per_correct: float


def uses_confidence(engine: str) -> bool:
    """Engines whose confidence ranks answers (curves, ECE, same coverage)."""
    return engine in CONFIDENCE_ENGINES or engine.startswith(CONFIDENCE_PREFIXES)


# Inputs -----------------------------------------------------------------------


def read_jsonl(path: Path) -> list[Row]:
    # Split on "\n" only: unescaped U+2028 and similar inside titles are data.
    # A line of JSON whitespace only, "\r" of CRLF included, is skipped.
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").split("\n")
            if line.strip(JSON_WHITESPACE)
        ]
    except (OSError, ValueError, RecursionError) as exc:
        raise InputError(f"{path}: {exc}") from exc
    if not all(isinstance(row, dict) for row in rows):
        raise InputError(f"{path}: every line must be a JSON object")
    return rows


def _parse_rows[T](path: Path, parse: Callable[[Row], T]) -> list[T]:
    items: list[T] = []
    for number, row in enumerate(read_jsonl(path), start=1):
        try:
            items.append(parse(row))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise InputError(f"{path}: row {number}: {exc!r}") from exc
    return items


def parse_case(row: Row) -> Case:
    gold, tags = row["gold"], row["tags"]
    if not (gold is None or isinstance(gold, str)):
        raise ValueError("gold must be text or null")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be a list of text")
    return Case(
        case_id=str(row["case_id"]),
        persona=str(row["persona"]),
        activity_id=str(row["activity_id"]),
        gold=gold,
        tags=frozenset(tags),
    )


def _is_kind(value: object, kind: str) -> bool:
    if kind == FLAG:
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if kind == TEXT:
        return isinstance(value, str)
    if kind == COUNT:
        return isinstance(value, int) and 0 <= value < MAX_COUNT
    if not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        return False
    return kind == NUMBER or value <= 1


def parse_result(row: Row) -> Result:
    """A result row whose fields have the contract's types; else ValueError."""
    for name, (kind, nullable) in RESULT_KINDS.items():
        value = row[name]
        if not (nullable and value is None) and not _is_kind(value, kind):
            raise ValueError(f"{name} must be {kind}{' or null' if nullable else ''}")
    return Result(**{name: row[name] for name in RESULT_KINDS})


def load_cases(path: Path) -> dict[str, Case]:
    cases: dict[str, Case] = {}
    for case in _parse_rows(path, parse_case):
        if case.case_id in cases:
            raise InputError(f"{path}: duplicate case_id {case.case_id!r}")
        cases[case.case_id] = case
    return cases


def latest_results(results: Iterable[Result]) -> list[Result]:
    """The last row of each ``(case_id, engine, batch_size, rep)`` key."""
    latest = {
        (result.case_id, result.engine, result.batch_size, result.rep): result
        for result in results
    }
    return [latest[key] for key in sorted(latest, key=lambda k: (k[1], k[2], k))]


def load_results(path: Path) -> list[Result]:
    return latest_results(_parse_rows(path, parse_result))


def join(cases: Mapping[str, Case], results: Iterable[Result]) -> list[Answer]:
    answers: list[Answer] = []
    for result in results:
        case = cases.get(result.case_id)
        if case is None:
            raise InputError(f"result for unknown case_id {result.case_id!r}")
        answers.append(Answer(case, result))
    return answers


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise InputError(f"{path}: {exc}") from exc


def load_prices(path: Path) -> dict[str, Price]:
    """Price tables by engine name; a missing price key is 0."""
    prices: dict[str, Price] = {}
    for engine, table in _load_toml(path).items():
        if not isinstance(table, dict) or not table.keys() <= PRICE_KEYS:
            raise InputError(
                f"{path}: [{engine!r}] keys must be among {sorted(PRICE_KEYS)}"
            )
        source_url = table.get("source_url")
        retrieved_on = table.get("retrieved_on")
        if not (source_url is None or isinstance(source_url, str)) or not (
            retrieved_on is None or isinstance(retrieved_on, str | date)
        ):
            raise InputError(
                f"{path}: [{engine!r}] source_url and retrieved_on are text"
            )
        price = Price(
            input_usd_per_mtok=_number(path, table.get("input_usd_per_mtok", 0)),
            output_usd_per_mtok=_number(path, table.get("output_usd_per_mtok", 0)),
            usd_per_request=_number(path, table.get("usd_per_request", 0)),
            source_url=source_url,
            retrieved_on=None if retrieved_on is None else str(retrieved_on),
        )
        if (
            min(
                price.input_usd_per_mtok,
                price.output_usd_per_mtok,
                price.usd_per_request,
            )
            < 0
        ):
            raise InputError(f"{path}: [{engine!r}] prices must not be negative")
        prices[engine] = price
    return prices


def _number(path: Path, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InputError(f"{path}: a {type(value).__name__} is not a number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise InputError(f"{path}: a number is too large") from exc
    if not math.isfinite(number):
        raise InputError(f"{path}: {value!r} is not a finite number")
    return number


def load_decision(path: Path) -> Decision:
    data = _load_toml(path)
    if set(data) != DECISION_KEYS:
        raise InputError(f"{path}: keys must be exactly {sorted(DECISION_KEYS)}")
    primary = data["primary"]
    if (
        not isinstance(primary, list)
        or not primary
        or not all(isinstance(name, str) for name in primary)
    ):
        raise InputError(f"{path}: primary must be a non-empty list of engine names")
    return Decision(
        primary=tuple(primary),
        min_accuracy_diff=_number(path, data["min_accuracy_diff"]),
        max_p95_latency_ms=_number(path, data["max_p95_latency_ms"]),
        max_usd_per_correct=_number(path, data["max_usd_per_correct"]),
    )


def resolve_decision(decision: Decision, answers: Sequence[Answer]) -> Decision:
    """Resolve a bare ``c`` and require the rows the verdicts compare."""
    engines = {answer.result.engine for answer in answers}
    primary = tuple(_resolve_engine(name, engines) for name in decision.primary)
    available = {
        answer.result.engine
        for answer in _personas(answers)
        if answer.result.batch_size == 1 and answer.result.rep == 0
    }
    if BASELINE_ENGINE not in available:
        raise InputError("--decision needs (a, batch 1, rep 0) rows of persona cases")
    missing = [
        engine
        for engine in primary
        if engine == BASELINE_ENGINE or engine not in available
    ]
    if missing:
        raise InputError(f"primary engines without (batch 1, rep 0) rows: {missing}")
    if len(set(primary)) != len(primary):
        raise InputError(f"primary names the same engine twice: {list(primary)}")
    return replace(decision, primary=primary)


def _resolve_engine(name: str, engines: set[str]) -> str:
    if name != "c":
        return name
    routes = sorted(engine for engine in engines if engine.startswith(C_PREFIX))
    if len(routes) != 1:
        raise InputError(f"primary 'c' needs exactly one c@* engine, found {routes}")
    return routes[0]


# Metrics ----------------------------------------------------------------------


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _personas(answers: Iterable[Answer]) -> list[Answer]:
    return [answer for answer in answers if answer.case.persona != OWNER]


def calls(results: Iterable[Result]) -> dict[str, Result]:
    """One row per ``call_id``; rows of a batch call share latency and cost."""
    by_call: dict[str, Result] = {}
    for result in results:
        by_call.setdefault(result.call_id, result)
    return by_call


def call_cost(result: Result, price: Price | None) -> float | None:
    if result.cost_usd is not None:
        return result.cost_usd
    if price is None:
        return None
    cost = price.usd_per_request
    for tokens, rate in (
        (result.input_tokens, price.input_usd_per_mtok),
        (result.output_tokens, price.output_usd_per_mtok),
    ):
        if rate == 0:
            continue
        if tokens is None:
            return None
        cost += tokens * rate / TOKENS_PER_PRICE_UNIT
    return cost


def total_cost(results: Iterable[Result], price: Price | None) -> float | None:
    """Cost of the calls, each once; ``None`` when any call has no cost."""
    total = 0.0
    for _, result in sorted(calls(results).items()):
        cost = call_cost(result, price)
        if cost is None:
            return None
        total += cost
    return total


def nearest_rank(values: Sequence[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered) / 100) - 1]


def call_latency(results: Iterable[Result], percentile: int) -> int | None:
    """Nearest-rank latency percentile over calls, each counted once."""
    latencies = [result.latency_ms for result in calls(results).values()]
    return nearest_rank(latencies, percentile)


def threshold_curve(answers: Sequence[Answer]) -> list[Row]:
    curve: list[Row] = []
    for step in range(THRESHOLD_STEPS + 1):
        threshold = step / THRESHOLD_STEPS
        kept = [
            answer
            for answer in answers
            if answer.result.answered
            and answer.result.confidence is not None
            and answer.result.confidence >= threshold
        ]
        curve.append(
            {
                "threshold": threshold,
                "coverage": _ratio(len(kept), len(answers)),
                "selective_accuracy": _ratio(
                    sum(answer.correct for answer in kept), len(kept)
                ),
            }
        )
    return curve


def ece(answers: Iterable[Answer]) -> float | None:
    """Expected calibration error of ``prob`` over error-free non-null answers."""
    bins: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for answer in answers:
        prob = answer.result.prob
        if answer.result.answered and prob is not None:
            bins[min(int(prob * ECE_BINS), ECE_BINS - 1)].append((prob, answer.correct))
    total = sum(len(members) for members in bins.values())
    if total == 0:
        return None
    error = 0.0
    for _, members in sorted(bins.items()):
        accuracy = sum(correct for _, correct in members) / len(members)
        mean_prob = sum(prob for prob, _ in members) / len(members)
        error += abs(accuracy - mean_prob) * len(members) / total
    return error


def persona_follow_rate(answers: Iterable[Answer]) -> float | None:
    """Share of personas_disagree activities whose scored persona cases are right.

    Like every metric, it counts the cases that have a row.
    """
    by_activity: dict[str, list[bool]] = defaultdict(list)
    for answer in answers:
        if "personas_disagree" in answer.case.tags:
            by_activity[answer.case.activity_id].append(answer.correct)
    followed = sum(all(correct) for correct in by_activity.values())
    return _ratio(followed, len(by_activity))


def repeat_agreement(
    answers: Iterable[Answer], repeats: Mapping[str, Sequence[str | None]]
) -> float | None:
    repeated = [
        repeats[answer.case.case_id]
        for answer in answers
        if len(repeats.get(answer.case.case_id, ())) >= 2
    ]
    agreed = sum(len(set(preds)) == 1 for preds in repeated)
    return _ratio(agreed, len(repeated))


def metrics(
    answers: Sequence[Answer],
    *,
    repeats: Mapping[str, Sequence[str | None]],
    price: Price | None,
) -> Row:
    """Metrics of one engine and batch size over the given rep-0 answers."""
    total = len(answers)
    answered = [answer for answer in answers if answer.result.answered]
    answered_correct = sum(answer.correct for answer in answered)
    gold_null = [answer for answer in answers if answer.case.gold is None]
    correct = sum(answer.correct for answer in answers)
    confidence = bool(answers) and uses_confidence(answers[0].result.engine)
    results = [answer.result for answer in answers]
    cost = total_cost(results, price)
    return {
        "cases": total,
        "coverage": _ratio(len(answered), total),
        "selective_accuracy": _ratio(answered_correct, len(answered)),
        "confident_wrong_rate": _ratio(len(answered) - answered_correct, total),
        "null_hit_rate": _ratio(sum(a.correct for a in gold_null), len(gold_null)),
        "exact_match": _ratio(correct, total),
        "error_rate": _ratio(sum(r.error is not None for r in results), total),
        "refusal_rate": _ratio(sum(r.refused for r in results), total),
        "threshold_curve": threshold_curve(answers) if confidence else None,
        "ece": ece(answers) if confidence else None,
        "persona_follow_rate": persona_follow_rate(answers),
        "latency_ms": {f"p{p}": call_latency(results, p) for p in PERCENTILES},
        "repeat_agreement": repeat_agreement(answers, repeats),
        "cost_usd": cost,
        "usd_per_correct": None if cost is None or correct == 0 else cost / correct,
    }


def engine_summary(group: Sequence[Answer], price: Price | None) -> Row:
    """Metrics of one engine and batch size: persona cases, each persona, tags."""
    repeats: dict[str, list[str | None]] = defaultdict(list)
    for answer in sorted(group, key=lambda a: (a.case.case_id, a.result.rep)):
        repeats[answer.case.case_id].append(answer.result.pred)
    first = [answer for answer in group if answer.result.rep == 0]
    overall = _personas(first)
    by_persona: dict[str, list[Answer]] = defaultdict(list)
    for answer in first:
        by_persona[answer.case.persona].append(answer)
    return {
        "engine": group[0].result.engine,
        "batch_size": group[0].result.batch_size,
        "price": None if price is None else asdict(price),
        "overall": metrics(overall, repeats=repeats, price=price),
        "personas": {
            name: metrics(members, repeats=repeats, price=price)
            for name, members in sorted(by_persona.items())
        },
        "tags": {
            tag: metrics(
                [a for a in overall if tag in a.case.tags],
                repeats=repeats,
                price=price,
            )
            for tag in TAGS
        },
    }


# Same-coverage comparison -----------------------------------------------------


def same_coverage(answers: Sequence[Answer], k: int) -> tuple[list[Answer], bool]:
    """Keep the k most confident answers; ``True`` when fewer than k exist."""
    answered = sorted(
        (answer for answer in answers if answer.result.answered),
        key=lambda a: (
            a.result.confidence is None,
            -(a.result.confidence or 0.0),
            a.case.case_id,
        ),
    )
    if len(answered) < k:
        return list(answers), True
    kept = {answer.case.case_id for answer in answered[:k]}
    return [
        answer
        if answer.case.case_id in kept or not answer.result.answered
        else Answer(answer.case, replace(answer.result, pred=None))
        for answer in answers
    ], False


def mcnemar_p(b: int, c: int) -> float:
    """Two-sided exact McNemar test: binomial on the discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1))
    return min(1.0, 2 * tail / 2**n)


def bootstrap_ci(
    pairs: Sequence[tuple[str, bool, bool]], *, seed: int, level: Fraction
) -> tuple[float, float] | None:
    """Percentile CI of other minus baseline accuracy, resampling activities.

    ``pairs`` holds ``(activity_id, baseline correct, other correct)``; the cases
    of one activity are drawn together.
    """
    by_activity: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for activity_id, baseline, other in pairs:
        counts = by_activity[activity_id]
        counts[0] += 1
        counts[1] += baseline
        counts[2] += other
    clusters = [tuple(by_activity[key]) for key in sorted(by_activity)]
    if not clusters:
        return None
    random = Random(seed)
    diffs: list[float] = []
    for _ in range(BOOTSTRAP_ROUNDS):
        sample = random.choices(clusters, k=len(clusters))
        cases = sum(cluster[0] for cluster in sample)
        diff = sum(cluster[2] - cluster[1] for cluster in sample) / cases
        diffs.append(diff)
    diffs.sort()
    tail = (1 - level) / 2
    lower = diffs[math.ceil(tail * BOOTSTRAP_ROUNDS) - 1]
    upper = diffs[math.ceil((1 - tail) * BOOTSTRAP_ROUNDS) - 1]
    return lower, upper


def compare(
    baseline: Sequence[Answer],
    other: Sequence[Answer],
    *,
    price: Price | None,
    seed: int,
    level: Fraction,
) -> Row:
    """Compare ``other`` with the baseline on the cases both have rows for."""
    base_by_case = {answer.case.case_id: answer for answer in baseline}
    other_by_case = {answer.case.case_id: answer for answer in other}
    paired = sorted(base_by_case.keys() & other_by_case.keys())
    base_paired = [base_by_case[case_id] for case_id in paired]
    other_paired = [other_by_case[case_id] for case_id in paired]
    k = sum(answer.result.answered for answer in base_paired)
    engine = other[0].result.engine
    matched, short = (
        same_coverage(other_paired, k)
        if uses_confidence(engine)
        else (other_paired, False)
    )
    pairs = [
        (base.case.activity_id, base.correct, mine.correct)
        for base, mine in zip(base_paired, matched, strict=True)
    ]
    b = sum(base and not mine for _, base, mine in pairs)
    c = sum(mine and not base for _, base, mine in pairs)
    ci = bootstrap_ci(pairs, seed=seed, level=level)
    cost = total_cost([answer.result for answer in other_paired], price)
    correct_before = sum(answer.correct for answer in other_paired)
    base_accuracy = _ratio(sum(base for _, base, _ in pairs), len(pairs))
    other_accuracy = _ratio(sum(mine for _, _, mine in pairs), len(pairs))
    return {
        "engine": engine,
        "batch_size": other[0].result.batch_size,
        "paired": len(paired),
        "unpaired": len(base_by_case.keys() ^ other_by_case.keys()),
        "k": k,
        "other_answered": sum(answer.result.answered for answer in matched),
        "coverage_short": short,
        "baseline_accuracy": base_accuracy,
        "other_accuracy": other_accuracy,
        "accuracy_diff": None
        if base_accuracy is None or other_accuracy is None
        else other_accuracy - base_accuracy,
        "mcnemar": {"b": b, "c": c, "p_value": mcnemar_p(b, c)},
        "ci": None if ci is None else {"lower": ci[0], "upper": ci[1]},
        "usd_per_correct": None
        if cost is None or correct_before == 0
        else cost / correct_before,
        "p95_latency_ms": call_latency([answer.result for answer in other], 95),
    }


def score(
    answers: Sequence[Answer],
    *,
    prices: Mapping[str, Price],
    seed: int,
    level: Fraction,
) -> Row:
    """Engine metrics and same-coverage comparisons; ``decision`` left empty."""
    groups: dict[tuple[str, int], list[Answer]] = defaultdict(list)
    for answer in answers:
        groups[answer.result.engine, answer.result.batch_size].append(answer)
    persona_rep0 = {
        key: _personas(a for a in group if a.result.rep == 0)
        for key, group in groups.items()
    }
    baseline = persona_rep0.get((BASELINE_ENGINE, 1), [])
    comparisons = [
        compare(
            baseline,
            persona_rep0[key],
            price=prices.get(key[0]),
            seed=seed,
            level=level,
        )
        for key in sorted(groups)
        if baseline and key != (BASELINE_ENGINE, 1) and persona_rep0[key]
    ]
    return {
        "seed": seed,
        "confidence_level": float(level),
        "engines": [
            engine_summary(groups[key], prices.get(key[0])) for key in sorted(groups)
        ],
        "comparisons": comparisons,
        "decision": None,
    }


# Decision ---------------------------------------------------------------------


def engine_label(engine: str) -> str:
    """``B`` for ``b``, ``C(route)`` for ``c@route``; other names as they are."""
    if engine == "b":
        return "B"
    if engine.startswith(C_PREFIX):
        return f"C({engine.removeprefix(C_PREFIX)})"
    return engine


def judge(comparison: Row, decision: Decision, *, p95: int | None) -> str:
    """Reject, then adopt, then inconclusive; a null cost or p95 only blocks adopt."""
    ci = comparison["ci"]
    cost = comparison["usd_per_correct"]
    if (
        (ci is not None and ci["upper"] < decision.min_accuracy_diff)
        or (p95 is not None and p95 > decision.max_p95_latency_ms)
        or (cost is not None and cost > decision.max_usd_per_correct)
    ):
        return REJECT
    if (
        ci is not None
        and ci["lower"] >= decision.min_accuracy_diff
        and cost is not None
        and p95 is not None
    ):
        return ADOPT
    return INCONCLUSIVE


def decide(
    comparisons: Sequence[Row],
    decision: Decision,
    *,
    latency: Sequence[Result] | None,
) -> Row:
    """Verdicts of the primary comparisons (batch 1) and the conclusion.

    With ``latency`` (``--latency-results``) p95 comes only from its batch-1 calls
    of the engine, ``None`` when it has none.
    """
    by_engine = {row["engine"]: row for row in comparisons if row["batch_size"] == 1}
    verdicts: list[Row] = []
    for engine in decision.primary:
        comparison = by_engine[engine]
        p95 = (
            comparison["p95_latency_ms"]
            if latency is None
            else call_latency(
                [r for r in latency if r.engine == engine and r.batch_size == 1], 95
            )
        )
        ci = comparison["ci"]
        verdicts.append(
            {
                "engine": engine,
                "label": engine_label(engine),
                "ci_lower": None if ci is None else ci["lower"],
                "ci_upper": None if ci is None else ci["upper"],
                "p95_latency_ms": p95,
                "usd_per_correct": comparison["usd_per_correct"],
                "verdict": judge(comparison, decision, p95=p95),
            }
        )
    return {
        "primary": list(decision.primary),
        "min_accuracy_diff": decision.min_accuracy_diff,
        "max_p95_latency_ms": decision.max_p95_latency_ms,
        "max_usd_per_correct": decision.max_usd_per_correct,
        "latency_source": "results" if latency is None else "latency_results",
        "verdicts": verdicts,
        "conclusion": conclude(verdicts),
    }


def conclude(verdicts: Sequence[Row]) -> str:
    adopted = [row for row in verdicts if row["verdict"] == ADOPT]
    if not adopted:
        if any(row["verdict"] == INCONCLUSIVE for row in verdicts):
            return UNDECIDED
        return KEEP_A
    best = min(adopted, key=lambda row: (-row["ci_lower"], row["usd_per_correct"]))
    return best["label"]


# Report -----------------------------------------------------------------------


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    # Any line break would end the table row.
    text = " ".join(str(value).splitlines())
    return text.replace("\\", "\\\\").replace("|", "\\|")


ENGINE_COLUMNS = (
    ("cases", "사례"),
    ("coverage", "coverage"),
    ("selective_accuracy", "selective_accuracy"),
    ("confident_wrong_rate", "확신 오답률"),
    ("null_hit_rate", "null 적중률"),
    ("exact_match", "exact_match"),
    ("error_rate", "오류율"),
    ("refusal_rate", "거부율"),
    ("ece", "ECE"),
    ("persona_follow_rate", "페르소나 추종률"),
    ("repeat_agreement", "반복 일치율"),
    ("usd_per_correct", "맞힌 1건당 USD"),
)
LIMITATIONS = (
    (
        "few-shot 예시는 앞선 모든 구간이 끝나는 즉시 확정됐다고 보고 `ended_at` 순서로 "
        "골랐다. 운영은 일부만 확정되고 확정 시각 순이다."
    ),
    "페르소나는 실제 사람보다 일관적이라 페르소나 사례의 결과는 상한이다.",
    (
        "같은 활동이 페르소나마다 사례가 되므로 McNemar p값은 낙관적일 수 있다. "
        "판정은 활동 단위 bootstrap CI로만 한다."
    ),
)


def _table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    lines.extend("| " + " | ".join(_fmt(cell) for cell in row) + " |" for row in rows)
    return lines


def _engine_lines(summary: Row, persona: str | None) -> list[str]:
    rows = []
    for entry in summary["engines"]:
        values = entry["overall"] if persona is None else entry["personas"].get(persona)
        if values is None or not values["cases"]:
            continue
        latency = values["latency_ms"]
        rows.append(
            [
                entry["engine"],
                entry["batch_size"],
                *(values[key] for key, _ in ENGINE_COLUMNS),
                *(latency[f"p{p}"] for p in PERCENTILES),
            ]
        )
    header = [
        "엔진",
        "batch",
        *(title for _, title in ENGINE_COLUMNS),
        *(f"p{p} ms" for p in PERCENTILES),
    ]
    return _table(header, rows)


def _comparison_lines(summary: Row) -> list[str]:
    if not summary["comparisons"]:
        return ["기준 `(a, batch 1, rep 0)` 행이 없어 비교하지 않았다."]
    rows = [
        [
            row["engine"],
            row["batch_size"],
            row["paired"],
            row["unpaired"],
            row["k"],
            row["coverage_short"],
            row["baseline_accuracy"],
            row["other_accuracy"],
            row["accuracy_diff"],
            None if row["ci"] is None else row["ci"]["lower"],
            None if row["ci"] is None else row["ci"]["upper"],
            row["mcnemar"]["b"],
            row["mcnemar"]["c"],
            row["mcnemar"]["p_value"],
        ]
        for row in summary["comparisons"]
    ]
    header = [
        "엔진",
        "batch",
        "짝",
        "뺀 사례",
        "k",
        "coverage_short",
        "기준 정확도",
        "상대 정확도",
        "차이",
        "CI 하한",
        "CI 상한",
        "McNemar b",
        "McNemar c",
        "p",
    ]
    return _table(header, rows)


def _decision_lines(decision: Row) -> list[str]:
    rows = [
        [
            row["label"],
            row["ci_lower"],
            row["ci_upper"],
            row["p95_latency_ms"],
            row["usd_per_correct"],
            row["verdict"],
        ]
        for row in decision["verdicts"]
    ]
    header = ["엔진", "CI 하한", "CI 상한", "p95 ms", "맞힌 1건당 USD", "판정"]
    return [
        f"- 결론: **{_fmt(decision['conclusion'])}**",
        (
            f"- 기준: 정확도 차이 ≥ {decision['min_accuracy_diff']}, "
            f"p95 ≤ {decision['max_p95_latency_ms']} ms, "
            f"맞힌 1건당 ≤ {decision['max_usd_per_correct']} USD"
        ),
        f"- p95 출처: {decision['latency_source']}",
        "",
        *_table(header, rows),
    ]


def _price_lines(summary: Row) -> list[str]:
    rows = [
        [entry["engine"], entry["price"]["source_url"], entry["price"]["retrieved_on"]]
        for entry in summary["engines"]
        if entry["price"] is not None and entry["batch_size"] == 1
    ]
    if not rows:
        return []
    return [
        "## 가격 출처",
        "",
        *_table(["엔진", "source_url", "retrieved_on"], rows),
        "",
    ]


def _limitations(summary: Row, *, has_owner: bool) -> list[str]:
    items = list(LIMITATIONS)
    if not has_owner:
        items.append("owner 직접 라벨이 없어 현실 성능은 측정하지 않았다.")
    if any(entry["engine"].endswith("@ollama") for entry in summary["engines"]):
        items.append(
            "`@ollama` 경로는 하네스 시운전용이며 정확도·ECE·판정을 인용하지 않는다."
        )
    return ["## 한계", "", *(f"- {item}" for item in items), ""]


def render_report(summary: Row) -> str:
    has_owner = any(OWNER in entry["personas"] for entry in summary["engines"])
    lines = [
        "# 라벨 제안 엔진 평가 결과",
        "",
        f"- 신뢰 수준: {_fmt(summary['confidence_level'])}, 시드: {summary['seed']}",
        (
            "- 지표는 rep 0 행으로 계산한다. 페르소나·태그별 분해와 문턱 곡선은 "
            "`summary.json`에 있다."
        ),
        "",
        "## 엔진별 지표(페르소나 사례)",
        "",
        *_engine_lines(summary, None),
        "",
    ]
    if has_owner:
        lines += ["## owner 사례(판정 제외)", "", *_engine_lines(summary, OWNER), ""]
    lines += ["## 같은 coverage 비교", "", *_comparison_lines(summary), ""]
    if summary["decision"] is not None:
        lines += ["## 판정", "", *_decision_lines(summary["decision"]), ""]
    lines += _price_lines(summary)
    lines += _limitations(summary, has_owner=has_owner)
    return "\n".join(lines)


# CLI --------------------------------------------------------------------------


def dump_summary(summary: Row) -> str:
    text = json.dumps(
        summary, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    )
    return text + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score label evaluation results.")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--latency-results", type=Path)
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--decision", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def build_summary(args: argparse.Namespace) -> Row:
    answers = join(load_cases(args.cases), load_results(args.results))
    latency = (
        None if args.latency_results is None else load_results(args.latency_results)
    )
    prices = {} if args.prices is None else load_prices(args.prices)
    decision = (
        None
        if args.decision is None
        else resolve_decision(load_decision(args.decision), answers)
    )
    level = (
        DEFAULT_LEVEL if decision is None else 1 - FAMILY_ALPHA / len(decision.primary)
    )
    summary = score(answers, prices=prices, seed=args.seed, level=level)
    if decision is not None:
        summary["decision"] = decide(summary["comparisons"], decision, latency=latency)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; rejected arguments or inputs exit with 2 and write nothing."""
    args = _parser().parse_args(argv)
    try:
        summary = build_summary(args)
        try:
            text = dump_summary(summary)
        except ValueError as exc:
            raise InputError(f"a total is not finite: {exc}") from exc
    except InputError as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return EXIT_USAGE
    report = render_report(summary)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(text, encoding="utf-8")
    (args.out / "report.md").write_text(report, encoding="utf-8")
    print(f"wrote summary.json and report.md to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
