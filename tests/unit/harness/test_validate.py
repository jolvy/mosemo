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

    code = full_validator.main(run, lambda: True)

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
    code = full_validator.main(lambda _: 0, lambda: True)

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

    code = full_validator.main(run, lambda: False)

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
