import asyncio
import json
import random
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from tests.support.label_eval import case_row, label_id, read_rows, write_cases

ROW_KEYS = {
    "case_id",
    "engine",
    "batch_size",
    "rep",
    "call_id",
    "pred",
    "confidence",
    "prob",
    "refused",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "attempts",
    "error",
}


class Recorder:
    """A FunctionModel body that answers from a queue and keeps what it saw."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.instructions: list[str | None] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.instructions.append(info.instructions)
        self.prompts.append(_user_prompt(messages))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])


def _user_prompt(messages: list[ModelMessage]) -> str:
    for part in messages[0].parts:
        if isinstance(part, UserPromptPart):
            return str(part.content)
    raise AssertionError("no user prompt")


def _cases(module: ModuleType, rows: list[dict[str, Any]]) -> list[Any]:
    return [module.parse_case(row) for row in rows]


def _run(
    module: ModuleType,
    engine: Any,
    rows: list[dict[str, Any]],
    out: Path,
    *,
    batch_size: int = 1,
    repeats: int = 1,
) -> list[dict[str, Any]]:
    calls = module.plan_calls(
        _cases(module, rows),
        engine=engine.name,
        batch_size=batch_size,
        repeats=repeats,
        done=set(),
    )
    pacer = module.Pacer(min_interval=0, clock=_zero, sleep=asyncio.sleep)
    asyncio.run(
        module.run_calls(engine, calls, batch_size=batch_size, out=out, pacer=pacer)
    )
    return read_rows(out)


def _zero() -> float:
    return 0.0


def _example(number: int, label: str | None) -> dict[str, Any]:
    return {
        "activity_id": str(uuid5(NAMESPACE_URL, f"example/{number}")),
        "activity": f"app_name: Example{number}",
        "label_id": None if label is None else label_id("p", label),
    }


def test_a_passes_the_suggestion_input_and_turns_answers_into_rows(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    examples = [_example(1, "휴식"), _example(2, None)]
    rows = [case_row(1, examples=examples), case_row(2, gold=None)]
    recorder = Recorder({"label_id": label_id("p", "휴식")}, {"label_id": None})
    engine = label_eval_run.SingleEngine(FunctionModel(recorder), max_attempts=9)

    results = _run(label_eval_run, engine, rows, tmp_path / "results.jsonl")

    assert json.loads(recorder.prompts[0]) == {
        "activity": "app_name: App1",
        "active_labels": [
            {"label_id": label_id("p", "공부"), "name": "공부"},
            {"label_id": label_id("p", "휴식"), "name": "휴식"},
        ],
        "confirmed_examples": [
            {"activity": e["activity"], "label_id": e["label_id"]} for e in examples
        ],
    }
    example = label_eval_run.suggestion_input(label_eval_run.parse_case(rows[0]))
    assert example.examples[0].confirmation_id == uuid5(
        NAMESPACE_URL, f"mosemo-label-eval/example/{examples[0]['activity_id']}"
    )
    assert [set(row) for row in results] == [ROW_KEYS, ROW_KEYS]
    first, second = results
    assert first["case_id"] == rows[0]["case_id"]
    assert first["pred"] == label_id("p", "휴식")
    assert second["pred"] is None
    assert first["call_id"] != second["call_id"]
    for row in results:
        assert row["engine"] == "a"
        assert row["batch_size"] == 1
        assert row["rep"] == 0
        assert row["confidence"] is None
        assert row["refused"] is False
        assert row["error"] is None
        assert row["cost_usd"] is None
        assert isinstance(row["latency_ms"], int)
        assert isinstance(row["input_tokens"], int)
        assert isinstance(row["output_tokens"], int)


def test_a_records_an_error_row_with_only_the_exception_type(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    outside = str(uuid5(NAMESPACE_URL, "not a candidate"))
    engine = label_eval_run.SingleEngine(
        FunctionModel(Recorder({"label_id": outside})), max_attempts=9
    )

    (row,) = _run(label_eval_run, engine, [case_row(1)], tmp_path / "results.jsonl")

    assert row["error"] == "UnexpectedModelBehavior"
    assert row["pred"] is None
    assert row["input_tokens"] is None
    assert row["attempts"] is None


def test_a_batch_answers_a_batch_in_one_call_sharing_the_call_id(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    rows = [
        case_row(1, examples=[_example(1, "휴식")]),
        case_row(2, examples=[_example(2, "공부")]),
        case_row(3),
    ]
    answer = [
        {"case_id": rows[0]["case_id"], "label_id": label_id("p", "공부")},
        {"case_id": rows[1]["case_id"], "label_id": None},
        {"case_id": rows[2]["case_id"], "label_id": label_id("p", "휴식")},
    ]
    recorder = Recorder({"response": answer})
    engine = label_eval_run.BatchEngine(FunctionModel(recorder), max_attempts=9)

    results = _run(
        label_eval_run, engine, rows, tmp_path / "results.jsonl", batch_size=3
    )

    assert len(recorder.prompts) == 1
    prompt = json.loads(recorder.prompts[0])
    assert [a["case_id"] for a in prompt["activities"]] == [r["case_id"] for r in rows]
    assert prompt["confirmed_examples"] == [
        {"activity": "app_name: Example1", "label_id": label_id("p", "휴식")}
    ]
    assert [row["pred"] for row in results] == [a["label_id"] for a in answer]
    assert len({row["call_id"] for row in results}) == 1
    assert {row["engine"] for row in results} == {"a-batch"}
    assert {row["batch_size"] for row in results} == {3}
    assert {row["attempts"] for row in results} == {1}
    assert len({(row["input_tokens"], row["output_tokens"]) for row in results}) == 1


def test_a_batch_uses_the_operational_instructions_with_the_case_id_line(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    single = Recorder({"label_id": None})
    batch = Recorder(
        {"response": [{"case_id": case_row(1)["case_id"], "label_id": None}]}
    )
    _run(
        label_eval_run,
        label_eval_run.SingleEngine(FunctionModel(single), max_attempts=9),
        [case_row(1)],
        tmp_path / "a.jsonl",
    )
    _run(
        label_eval_run,
        label_eval_run.BatchEngine(FunctionModel(batch), max_attempts=9),
        [case_row(1)],
        tmp_path / "a-batch.jsonl",
    )

    assert batch.instructions == [
        f"{single.instructions[0]} 각 activity마다 case_id와 함께 답한다"
    ]


@pytest.mark.parametrize("broken", ["missing", "duplicate", "outside"])
def test_a_batch_retries_missing_duplicate_and_outside_answers(
    label_eval_run: ModuleType, tmp_path: Path, broken: str
) -> None:
    rows = [case_row(1), case_row(2)]
    good = [
        {"case_id": rows[0]["case_id"], "label_id": label_id("p", "공부")},
        {"case_id": rows[1]["case_id"], "label_id": None},
    ]
    bad = {
        "missing": good[:1],
        "duplicate": [good[0], good[0], good[1]],
        "outside": [
            good[0],
            {"case_id": rows[1]["case_id"], "label_id": label_id("other", "공부")},
        ],
    }[broken]
    recorder = Recorder({"response": bad}, {"response": good})
    engine = label_eval_run.BatchEngine(FunctionModel(recorder), max_attempts=9)

    results = _run(
        label_eval_run, engine, rows, tmp_path / "results.jsonl", batch_size=2
    )

    assert len(recorder.prompts) == 2
    assert [row["pred"] for row in results] == [label_id("p", "공부"), None]
    assert {row["attempts"] for row in results} == {2}
    assert {row["error"] for row in results} == {None}


def test_a_batch_gives_up_after_three_attempts(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    rows = [case_row(1), case_row(2)]
    recorder = Recorder(
        {"response": [{"case_id": rows[0]["case_id"], "label_id": None}]}
    )
    engine = label_eval_run.BatchEngine(FunctionModel(recorder), max_attempts=9)

    results = _run(
        label_eval_run, engine, rows, tmp_path / "results.jsonl", batch_size=2
    )

    assert len(recorder.prompts) == 3
    assert [row["error"] for row in results] == ["UnexpectedModelBehavior"] * 2
    assert len({row["call_id"] for row in results}) == 1


def test_oracle_answers_the_gold_label_with_confidence_one(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    rows = [case_row(1), case_row(2, gold=None), case_row(3, gold="휴식")]
    cases = write_cases(tmp_path / "cases.jsonl", rows)
    out = tmp_path / "results.jsonl"

    code = label_eval_run.main(
        ["--cases", str(cases), "--engine", "oracle", "--out", str(out)]
    )

    assert code == 0
    results = read_rows(out)
    assert [row["pred"] for row in results] == [row["gold"] for row in rows]
    assert {row["confidence"] for row in results} == {1.0}
    assert {row["engine"] for row in results} == {"oracle"}
    assert {row["attempts"] for row in results} == {0}


def test_random_follows_the_seed_formula_and_repeats_identically(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    rows = [case_row(number) for number in range(20)]
    cases = write_cases(tmp_path / "cases.jsonl", rows)
    outputs = []
    for name in ("first.jsonl", "second.jsonl"):
        out = tmp_path / name
        argv = ["--cases", str(cases), "--engine", "random", "--seed", "7"]
        assert label_eval_run.main([*argv, "--repeats", "2", "--out", str(out)]) == 0
        outputs.append(read_rows(out))

    first, second = outputs
    for row in first:
        choices = [label_id("p", "공부"), label_id("p", "휴식"), None]
        expected = random.Random(f"7/{row['case_id']}/{row['rep']}").choice(choices)
        assert row["pred"] == expected
        assert row["confidence"] is None
    assert len(first) == 40
    assert {row["pred"] for row in first} == {*choices}

    def stable(row: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in row.items() if k not in {"call_id", "latency_ms"}}

    assert [stable(row) for row in first] == [stable(row) for row in second]


def test_every_row_of_the_four_engines_has_a_null_prob(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    rows = [case_row(1), case_row(2)]
    answer = [{"case_id": r["case_id"], "label_id": None} for r in rows]
    engines = [
        label_eval_run.SingleEngine(
            FunctionModel(Recorder({"label_id": None})), max_attempts=9
        ),
        label_eval_run.BatchEngine(
            FunctionModel(Recorder({"response": answer})), max_attempts=9
        ),
        label_eval_run.OracleEngine(),
        label_eval_run.RandomEngine(seed=0),
    ]
    for engine in engines:
        results = _run(label_eval_run, engine, rows, tmp_path / f"{engine.name}.jsonl")
        assert [row["prob"] for row in results] == [None, None]


@pytest.mark.parametrize("engine_name", ["a", "a-batch"])
def test_a_engines_record_a_timeout_row(
    label_eval_run: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
) -> None:
    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        await asyncio.sleep(1)
        raise AssertionError("the timeout should cut the call")

    model = FunctionModel(slow)
    if engine_name == "a":
        # The operational adapter's own 30-second timeout, shortened.
        real_timeout = asyncio.timeout
        monkeypatch.setattr(
            "mosemo.labeling.infrastructure.suggestions.asyncio.timeout",
            lambda _seconds: real_timeout(0.05),
        )
        engine = label_eval_run.SingleEngine(model, max_attempts=9)
    else:
        monkeypatch.setattr(label_eval_run, "TIMEOUT_SECONDS", 0.05)
        engine = label_eval_run.BatchEngine(model, max_attempts=9)

    (row,) = _run(label_eval_run, engine, [case_row(1)], tmp_path / "r.jsonl")

    assert row["error"] == "TimeoutError"
    assert row["pred"] is None
