import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx2
import pytest
from pydantic_ai.models import infer_model

from tests.support.label_eval import case_row, read_rows, write_cases

OPENAI_KEY = "sk-test-openai-secret-value"
GOOGLE_KEY = "test-google-secret-value"


def _argv(cases: Path, out: Path, *extra: str) -> list[str]:
    return ["--cases", str(cases), "--out", str(out), *extra]


def _row(case_id: str, *, rep: int = 0, error: str | None = None) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "engine": "oracle",
        "batch_size": 1,
        "rep": rep,
        "call_id": "old",
        "pred": None,
        "confidence": 1.0,
        "prob": None,
        "refused": False,
        "latency_ms": 0,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "attempts": 0,
        "error": error,
    }


class Unavailable:
    """A transport handler that always answers 503 and counts requests."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(
            503,
            headers={"retry-after-ms": "1"},
            json={"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}},
        )


def test_resume_skips_keys_with_a_clean_row_and_recalls_error_only_keys(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    rows = [case_row(1), case_row(2), case_row(3)]
    cases = write_cases(tmp_path / "cases.jsonl", rows)
    out = tmp_path / "results.jsonl"
    done, failed, new = (row["case_id"] for row in rows)
    previous = [
        _row(done, error="ModelHTTPError"),
        _row(done),
        _row(failed, error="TimeoutError"),
        _row(failed, error="ModelHTTPError"),
    ]
    out.write_text("".join(json.dumps(r) + "\n" for r in previous), encoding="utf-8")

    assert label_eval_run.main(_argv(cases, out, "--engine", "oracle")) == 0

    results = read_rows(out)
    assert results[: len(previous)] == previous
    added = results[len(previous) :]
    assert [row["case_id"] for row in added] == [failed, new]
    assert {row["error"] for row in added} == {None}


def test_resume_key_includes_engine_batch_size_and_rep(
    label_eval_run: ModuleType,
) -> None:
    cases = [label_eval_run.parse_case(case_row(1))]
    case_id = cases[0].case_id
    done = label_eval_run.completed_keys([_row(case_id)], path=Path("r.jsonl"))

    def planned(engine: str, batch_size: int) -> list[tuple[str, int]]:
        calls = label_eval_run.plan_calls(
            cases, engine=engine, batch_size=batch_size, repeats=2, done=done
        )
        return [(call.cases[0].case_id, call.rep) for call in calls]

    assert planned("oracle", 1) == [(case_id, 1)]
    assert planned("oracle", 2) == [(case_id, 0), (case_id, 1)]
    assert planned("random", 1) == [(case_id, 0), (case_id, 1)]


def test_batches_cut_the_run_cases_per_persona_in_case_order(
    label_eval_run: ModuleType,
) -> None:
    rows = [case_row(n, persona="p" if n % 2 else "q") for n in range(1, 8)]
    cases = [label_eval_run.parse_case(row) for row in rows]
    done = label_eval_run.completed_keys(
        [_row(rows[0]["case_id"]) | {"batch_size": 2}], path=Path("r.jsonl")
    )

    calls = label_eval_run.plan_calls(
        cases, engine="oracle", batch_size=2, repeats=1, done=done
    )

    assert [[case.case_id for case in call.cases] for call in calls] == [
        [rows[1]["case_id"], rows[3]["case_id"]],
        [rows[5]["case_id"]],
        [rows[2]["case_id"], rows[4]["case_id"]],
        [rows[6]["case_id"]],
    ]


@pytest.mark.parametrize(
    ("label_model", "max_requests", "allowed"),
    [
        ("google:gemini-test", "5", False),
        ("google:gemini-test", "6", True),
        ("openai:gpt-test", "17", False),
    ],
)
def test_max_requests_refuses_the_run_before_any_call(
    label_eval_run: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    label_model: str,
    max_requests: str,
    allowed: bool,
) -> None:
    monkeypatch.setenv("LABEL_MODEL", label_model)
    monkeypatch.setenv("GOOGLE_API_KEY", GOOGLE_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    cases = write_cases(tmp_path / "cases.jsonl", [case_row(1), case_row(2)])
    out = tmp_path / "results.jsonl"
    handler = Unavailable()
    argv = _argv(cases, out, "--engine", "a", "--max-requests", max_requests)

    code = label_eval_run.main(
        argv, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )

    if allowed:
        assert code == 0
        assert len(read_rows(out)) == 2
    else:
        assert code == 2
        assert handler.requests == []
        assert not out.exists()
        assert "--max-requests" in capsys.readouterr().err


def test_max_requests_counts_batch_calls_and_is_required_for_model_engines(
    label_eval_run: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LABEL_MODEL", "google:gemini-test")
    cases = write_cases(tmp_path / "cases.jsonl", [case_row(n) for n in range(5)])
    out = tmp_path / "results.jsonl"

    # 5 cases in batches of 2 are 3 calls, each up to 3 requests.
    batch = _argv(cases, out, "--engine", "a-batch", "--batch-size", "2")
    assert label_eval_run.main([*batch, "--max-requests", "8"]) == 2
    assert label_eval_run.main(batch) == 2
    assert label_eval_run.main(_argv(cases, out, "--engine", "a")) == 2
    assert not out.exists()


def test_sample_picks_the_same_cases_for_every_engine(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    rows = [case_row(n) for n in range(30)]
    cases = write_cases(tmp_path / "cases.jsonl", rows)
    picked = {}
    for engine, seed in (("oracle", "3"), ("random", "3"), ("oracle", "4")):
        out = tmp_path / f"{engine}-{seed}.jsonl"
        argv = _argv(cases, out, "--engine", engine, "--sample", "5", "--seed", seed)
        assert label_eval_run.main([*argv, "--repeats", "2"]) == 0
        results = read_rows(out)
        assert len(results) == 10
        picked[engine, seed] = {row["case_id"] for row in results}

    assert len(picked["oracle", "3"]) == 5
    assert picked["oracle", "3"] == picked["random", "3"]
    assert picked["oracle", "3"] != picked["oracle", "4"]


@pytest.mark.parametrize(
    ("label_model", "limit"), [("openai:gpt-test", 9), ("google:gemini-test", 3)]
)
@pytest.mark.parametrize("engine", ["a", "a-batch"])
def test_a_makes_at_most_the_bounded_requests_and_never_shows_the_key(
    label_eval_run: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    label_model: str,
    limit: int,
    engine: str,
) -> None:
    monkeypatch.setenv("LABEL_MODEL", label_model)
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    monkeypatch.setenv("GOOGLE_API_KEY", GOOGLE_KEY)
    cases = write_cases(tmp_path / "cases.jsonl", [case_row(1)])
    out = tmp_path / "results.jsonl"
    handler = Unavailable()
    argv = _argv(cases, out, "--engine", engine, "--max-requests", str(limit))

    code = label_eval_run.main(
        argv, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )

    assert code == 0
    assert 1 <= len(handler.requests) <= limit
    (row,) = read_rows(out)
    assert row["error"] == "ModelHTTPError"
    captured = capsys.readouterr()
    shown = out.read_text(encoding="utf-8") + captured.out + captured.err
    for key in (OPENAI_KEY, GOOGLE_KEY):
        assert key not in shown


@pytest.mark.parametrize(
    "label_model", [None, "anthropic:claude-test", "openai", OPENAI_KEY]
)
def test_unsupported_label_model_is_refused(
    label_eval_run: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    label_model: str | None,
) -> None:
    if label_model is None:
        monkeypatch.delenv("LABEL_MODEL", raising=False)
    else:
        monkeypatch.setenv("LABEL_MODEL", label_model)
    cases = write_cases(tmp_path / "cases.jsonl", [case_row(1)])
    out = tmp_path / "results.jsonl"

    argv = _argv(cases, out, "--engine", "a", "--max-requests", "100")
    assert label_eval_run.main(argv) == 2
    assert not out.exists()
    assert OPENAI_KEY not in capsys.readouterr().err


@pytest.mark.parametrize("label_model", ["openai:gpt-test", "google:gemini-test"])
def test_built_model_matches_pydantic_ai_inference(
    label_eval_run: ModuleType, monkeypatch: pytest.MonkeyPatch, label_model: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    monkeypatch.setenv("GOOGLE_API_KEY", GOOGLE_KEY)

    built = label_eval_run.build_model(label_model)
    inferred = infer_model(label_model)

    assert type(built) is type(inferred)
    assert type(built._provider) is type(inferred._provider)
    assert built.model_name == inferred.model_name


class FakeMonotonic:
    """A seconds clock that only moves when the fake sleep waits."""

    def __init__(self) -> None:
        self.now = 100.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


class StartRecorder:
    """The oracle engine, noting each call start and taking `work` seconds."""

    name = "oracle"
    max_attempts = 0

    def __init__(
        self, module: ModuleType, clock: FakeMonotonic, *, work: float
    ) -> None:
        self.inner = module.OracleEngine()
        self.clock = clock
        self.work = work
        self.starts: list[float] = []

    async def answer(self, cases: Sequence[Any], *, rep: int) -> Any:
        self.starts.append(self.clock())
        self.clock.now += self.work
        return await self.inner.answer(cases, rep=rep)


@pytest.mark.parametrize("work", [0.0, 0.25, 1.5])
def test_call_starts_are_at_least_min_interval_apart_on_the_injected_clock(
    label_eval_run: ModuleType, tmp_path: Path, work: float
) -> None:
    clock = FakeMonotonic()
    engine = StartRecorder(label_eval_run, clock, work=work)
    cases = [label_eval_run.parse_case(case_row(n)) for n in range(4)]
    calls = label_eval_run.plan_calls(
        cases, engine="oracle", batch_size=1, repeats=1, done=set()
    )
    pacer = label_eval_run.Pacer(min_interval=0.5, clock=clock, sleep=clock.sleep)

    asyncio.run(
        label_eval_run.run_calls(
            engine, calls, batch_size=1, out=tmp_path / "r.jsonl", pacer=pacer
        )
    )

    gaps = [b - a for a, b in zip(engine.starts, engine.starts[1:], strict=False)]
    assert len(gaps) == 3
    assert all(gap >= 0.5 for gap in gaps)
    assert clock.waits == ([] if work >= 0.5 else [0.5 - work] * 3)


def test_min_interval_ms_paces_the_cli_run_on_the_injected_clock(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    clock = FakeMonotonic()
    cases = write_cases(tmp_path / "cases.jsonl", [case_row(n) for n in range(3)])
    argv = _argv(cases, tmp_path / "r.jsonl", "--engine", "random")

    code = label_eval_run.main(
        [*argv, "--min-interval-ms", "250"], clock=clock, sleep=clock.sleep
    )

    assert code == 0
    assert clock.waits == [0.25, 0.25]


@pytest.mark.parametrize("error", [httpx2.ConnectError, httpx2.TooManyRedirects])
def test_google_request_errors_become_error_rows_and_the_run_goes_on(
    label_eval_run: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: type[httpx2.RequestError],
) -> None:
    monkeypatch.setenv("LABEL_MODEL", "google:gemini-test")
    monkeypatch.setenv("GOOGLE_API_KEY", GOOGLE_KEY)
    cases = write_cases(tmp_path / "cases.jsonl", [case_row(1), case_row(2)])
    out = tmp_path / "results.jsonl"
    requests: list[httpx2.Request] = []

    def unreachable(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        raise error("unreachable", request=request)

    code = label_eval_run.main(
        _argv(cases, out, "--engine", "a", "--max-requests", "6"),
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(unreachable)),
    )

    assert code == 0
    assert len(requests) == 2
    assert [row["error"] for row in read_rows(out)] == [error.__name__] * 2
    captured = capsys.readouterr()
    shown = out.read_text(encoding="utf-8") + captured.out + captured.err
    assert GOOGLE_KEY not in shown


def test_latency_does_not_include_the_interval_wait(
    label_eval_run: ModuleType, tmp_path: Path
) -> None:
    cases = write_cases(tmp_path / "cases.jsonl", [case_row(1), case_row(2)])
    out = tmp_path / "results.jsonl"

    argv = _argv(cases, out, "--engine", "oracle", "--min-interval-ms", "600")
    assert label_eval_run.main(argv) == 0

    assert [row["latency_ms"] < 300 for row in read_rows(out)] == [True, True]


@pytest.mark.parametrize(
    "problem",
    ["a-batch-size", "bad-label-id", "bad-result-row", "bad-utf8", "missing-api-key"],
)
def test_bad_runs_are_refused_with_exit_two_before_any_call(
    label_eval_run: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    monkeypatch.setenv("LABEL_MODEL", "openai:gpt-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    row = case_row(1)
    if problem == "bad-label-id":
        row["labels"][0]["label_id"] = "not-a-uuid"
    cases = write_cases(tmp_path / "cases.jsonl", [row])
    out = tmp_path / "results.jsonl"
    if problem == "bad-result-row":
        out.write_text('{"case_id": "x"}\n', encoding="utf-8")
    if problem == "bad-utf8":
        out.write_bytes(b'{"case_id": "\xff"}\n')
    engine = ["--engine", "oracle"]
    if problem in {"a-batch-size", "missing-api-key"}:
        engine = ["--engine", "a", "--max-requests", "9"]
    if problem == "a-batch-size":
        engine += ["--batch-size", "2"]
    handler = Unavailable()

    code = label_eval_run.main(
        _argv(cases, out, *engine),
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )

    assert code == 2
    assert handler.requests == []
    assert out.exists() == (problem in {"bad-result-row", "bad-utf8"})
