"""커밋 전 전체 검증을 실행하고 마지막 줄에 `VERIFY_RESULT` JSON으로 판정한다.

앞 검사가 실패해도 모든 검사를 실행한다. `poe coverage` 직전에 `docker info`가 실패하면
coverage를 실행하지 않고 `not_run`에 둔다. `failed`와 `not_run`이 모두 비었을 때만 종료
코드 0이다. 끝나면 지표 로그에 `verify` 줄을 남긴다. 기록 실패는 종료 코드와
`VERIFY_RESULT`를 바꾸지 않고 stderr 한 줄만 남긴다.
"""

import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
HOOKS = str(ROOT / ".codex/hooks")
if HOOKS not in sys.path:
    sys.path.insert(0, HOOKS)

import stop_metrics

SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
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
    durations: dict[str, float] = field(default_factory=dict)

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
            continue
        started = time.monotonic()
        code = run(command)
        result.durations[name] = round(time.monotonic() - started, 3)
        (result.passed if code == 0 else result.failed).append(name)
    return result


def verify_fields(
    result: VerifyResult, *, env: Mapping[str, str], git: Mapping[str, Any]
) -> dict[str, Any]:
    """`verify` 줄의 필드(`at` 제외). `failed`·`not_run`은 `VERIFY_RESULT`와 같다."""
    session = env.get(SESSION_VARIABLE) or None
    source = stop_metrics.Source(session=session, agent="claude" if session else None)
    return {
        **stop_metrics.common_fields("verify", source=source, git=git),
        "ok": result.ok,
        "failed": result.failed,
        "not_run": result.not_run,
        "durations": result.durations,
    }


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


def git_query(arguments: list[str]) -> str:
    """지표 기록용 git 조회. 실패는 `OSError`로 알린다."""
    try:
        return subprocess.run(
            ["git", *arguments], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except subprocess.SubprocessError as error:
        raise OSError(error) from error


def record_verify(result: VerifyResult) -> None:
    stop_metrics.record(
        lambda: verify_fields(
            result, env=os.environ, git=stop_metrics.query_git_facts(git_query)
        ),
        git=git_query,
    )


def main(
    run: Callable[[str], int] = run_command,
    docker_available: Callable[[], bool] = docker_running,
    *,
    record: Callable[[VerifyResult], None] = record_verify,
) -> int:
    result = verify(run, docker_available)
    for line in summary(result):
        print(line)
    print(result.line(), flush=True)
    try:
        record(result)
    # 기록 실패는 판정을 바꾸지 않는다.
    except stop_metrics.RECORD_FAILURES as error:
        print(f"지표 로그 기록 실패: {type(error).__name__}: {error}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
