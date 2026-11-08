"""커밋 전 전체 검증을 실행하고 마지막 줄에 `VERIFY_RESULT` JSON으로 판정한다.

앞 검사가 실패해도 모든 검사를 실행한다. `poe coverage` 직전에 `docker info`가 실패하면
coverage를 실행하지 않고 `not_run`에 둔다. `failed`와 `not_run`이 모두 비었을 때만 종료
코드 0이다.
"""

import json
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ".codex/skills/verification/scripts"
COVERAGE = "poe coverage"
# 이전 validate.sh의 명령과 순서 그대로다. 루트 `AGENTS.md` 「빌드와 검증」 명령을 모두 담는다.
CHECKS: tuple[tuple[str, str], ...] = (
    ("validate_docs.py", f"uv run --locked python {SCRIPTS}/validate_docs.py"),
    (
        "validate_architecture.py",
        f"uv run --locked python {SCRIPTS}/validate_architecture.py",
    ),
    (
        "validate_conventions.py",
        f"uv run --locked python {SCRIPTS}/validate_conventions.py",
    ),
    ("ruff check .", "uv run --locked ruff check ."),
    (
        "complexipy src --max-complexity-allowed 15 --isolated --no-ignore",
        (
            "uv run --locked complexipy src --max-complexity-allowed 15 "
            "--isolated --no-ignore"
        ),
    ),
    ("ruff format --check .", "uv run --locked ruff format --check ."),
    ("ty check", "uv run --locked ty check"),
    ("poe lint-imports", "uv run --locked poe lint-imports"),
    ("uv build", "uv build"),
    (COVERAGE, "uv run --locked poe coverage"),
    ("git diff --check", "git diff --check"),
    ("git diff --cached --check", "git diff --cached --check"),
)


@dataclass
class VerifyResult:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    not_run: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed and not self.not_run

    def line(self) -> str:
        payload = {
            "ok": self.ok,
            "failed": self.failed,
            "not_run": self.not_run,
            "passed": len(self.passed),
        }
        return f"VERIFY_RESULT {json.dumps(payload, ensure_ascii=False)}"


def verify(
    run: Callable[[str], int], docker_available: Callable[[], bool]
) -> VerifyResult:
    """`run`은 명령 문자열을 실행해 종료 코드를 돌려준다. 실패해도 다음 검사로 넘어간다."""
    result = VerifyResult()
    for name, command in CHECKS:
        if name == COVERAGE and not docker_available():
            result.not_run.append({"check": name, "reason": "docker"})
        elif run(command) == 0:
            result.passed.append(name)
        else:
            result.failed.append(name)
    return result


def summary(result: VerifyResult) -> list[str]:
    skipped = {item["check"]: item["reason"] for item in result.not_run}
    lines = ["", "== 검증 요약 =="]
    for name, _ in CHECKS:
        if name in skipped:
            lines.append(f"NOT RUN  {name} ({skipped[name]})")
        elif name in result.failed:
            lines.append(f"FAIL     {name}")
        else:
            lines.append(f"PASS     {name}")
    return lines


def run_command(command: str) -> int:
    print(f"\n$ {command}", flush=True)
    try:
        return subprocess.run(shlex.split(command), cwd=ROOT, check=False).returncode
    except FileNotFoundError as error:
        print(error, file=sys.stderr, flush=True)
        return 127


def docker_running() -> bool:
    try:
        completed = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return False
    return completed.returncode == 0


def main(
    run: Callable[[str], int] = run_command,
    docker_available: Callable[[], bool] = docker_running,
) -> int:
    result = verify(run, docker_available)
    for line in summary(result):
        print(line)
    print(result.line(), flush=True)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
