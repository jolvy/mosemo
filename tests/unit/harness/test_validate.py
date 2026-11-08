"""전체 검증 실행기 `validate.py`의 판정과 `VERIFY_RESULT` 출력."""

import json
from types import ModuleType

import pytest

EXPECTED_COMMANDS = [
    "uv run --locked python .codex/skills/verification/scripts/validate_docs.py",
    "uv run --locked python .codex/skills/verification/scripts/validate_architecture.py",
    "uv run --locked python .codex/skills/verification/scripts/validate_conventions.py",
    "uv run --locked ruff check .",
    "uv run --locked complexipy src --max-complexity-allowed 15 --isolated --no-ignore",
    "uv run --locked ruff format --check .",
    "uv run --locked ty check",
    "uv run --locked poe lint-imports",
    "uv build",
    "uv run --locked poe coverage",
    "git diff --check",
    "git diff --cached --check",
]
COVERAGE_COMMAND = "uv run --locked poe coverage"
GIT = {"harness_rev": "abc1234", "harness_dirty": False, "branch": "feat/x"}
VERIFY_KEYS = [
    "event",
    "session",
    "agent",
    "model",
    "harness_rev",
    "harness_dirty",
    "branch",
    "ok",
    "failed",
    "not_run",
    "durations",
]


def ignore(_: object) -> None:
    pass


def last_result(output: str) -> dict:
    last = output.rstrip("\n").splitlines()[-1]
    prefix = "VERIFY_RESULT "
    assert last.startswith(prefix)
    return json.loads(last.removeprefix(prefix))


def test_runs_every_check_after_a_failure(
    full_validator: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    third = EXPECTED_COMMANDS[2]

    def run(command: str) -> int:
        calls.append(command)
        return 1 if command == third else 0

    code = full_validator.main(run, lambda: True, record=ignore)

    assert calls == EXPECTED_COMMANDS
    assert code == 1
    assert last_result(capsys.readouterr().out) == {
        "ok": False,
        "failed": ["validate_conventions.py"],
        "not_run": [],
        "passed": 11,
    }


def test_all_checks_pass(
    full_validator: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    code = full_validator.main(lambda _: 0, lambda: True, record=ignore)

    assert code == 0
    assert last_result(capsys.readouterr().out) == {
        "ok": True,
        "failed": [],
        "not_run": [],
        "passed": 12,
    }


def test_docker_unavailable_skips_coverage(
    full_validator: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    def run(command: str) -> int:
        calls.append(command)
        return 0

    code = full_validator.main(run, lambda: False, record=ignore)

    assert COVERAGE_COMMAND not in calls
    assert len(calls) == 11
    assert code == 1
    output = capsys.readouterr().out
    assert "NOT RUN  poe coverage (docker)" in output
    assert last_result(output) == {
        "ok": False,
        "failed": [],
        "not_run": [{"check": "poe coverage", "reason": "docker"}],
        "passed": 11,
    }


def test_check_commands_match_fixed_list_in_order(
    full_validator: ModuleType,
) -> None:
    assert [command for _, command in full_validator.CHECKS] == EXPECTED_COMMANDS


def test_verify_line_without_session_variable(full_validator: ModuleType) -> None:
    result = full_validator.verify(lambda _: 0, lambda: False)

    fields = full_validator.verify_fields(result, env={}, git=GIT)

    assert list(fields) == VERIFY_KEYS
    assert fields["event"] == "verify"
    assert fields["session"] is None
    assert fields["agent"] == "unknown"
    assert fields["model"] is None
    assert fields["harness_rev"] == "abc1234"
    assert fields["ok"] is False
    assert fields["failed"] == []
    assert fields["not_run"] == [{"check": "poe coverage", "reason": "docker"}]
    assert set(fields["durations"]) == {
        name for name, _ in full_validator.CHECKS if name != "poe coverage"
    }
    assert all(isinstance(value, float) for value in fields["durations"].values())


def test_verify_line_with_session_variable(full_validator: ModuleType) -> None:
    third = EXPECTED_COMMANDS[2]
    result = full_validator.verify(lambda c: 1 if c == third else 0, lambda: True)

    fields = full_validator.verify_fields(
        result, env={"CLAUDE_CODE_SESSION_ID": "s-1"}, git=GIT
    )

    assert list(fields) == VERIFY_KEYS
    assert fields["session"] == "s-1"
    assert fields["agent"] == "claude"
    assert fields["model"] is None
    assert fields["failed"] == ["validate_conventions.py"]
    assert fields["not_run"] == []
    assert len(fields["durations"]) == len(EXPECTED_COMMANDS)


def test_verify_line_matches_verify_result(
    full_validator: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded: list[dict] = []

    def record(result: object) -> None:
        recorded.append(full_validator.verify_fields(result, env={}, git=GIT))

    full_validator.main(lambda _: 0, lambda: False, record=record)

    printed = last_result(capsys.readouterr().out)
    assert recorded[0]["ok"] == printed["ok"]
    assert recorded[0]["failed"] == printed["failed"]
    assert recorded[0]["not_run"] == printed["not_run"]


@pytest.mark.parametrize("docker", [True, False])
def test_record_failure_keeps_exit_code_and_result(
    full_validator: ModuleType, capsys: pytest.CaptureFixture[str], docker: bool
) -> None:
    full_validator.main(lambda _: 0, lambda: docker, record=ignore)
    expected = last_result(capsys.readouterr().out)
    expected_code = 0 if docker else 1

    def broken(_: object) -> None:
        raise OSError("disk full")

    code = full_validator.main(lambda _: 0, lambda: docker, record=broken)

    captured = capsys.readouterr()
    assert code == expected_code
    assert last_result(captured.out) == expected
    assert captured.err.splitlines() == ["지표 로그 기록 실패: OSError: disk full"]
