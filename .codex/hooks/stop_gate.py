"""종료 전 hook: 바뀐 Python 파일을 포맷하고 스타일 검사와 영향 범위 테스트로 종료를 판정한다.

Claude Code와 Codex의 `Stop` 입력을 받는다. stdout에는 쓰지 않는다. 막을 때는 stderr에
지시문과 실패한 검사의 출력을 쓰고 exit 2, 통과하면 exit 0이다.
"""

import contextlib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import review_metrics
import stop_metrics

UV_RUN = ("uv", "run", "--locked")
# hook 설정 timeout(300초)을 넘기면 도구가 통과로 처리하므로 내부 제한을 먼저 건다.
TIME_LIMIT = 240.0
RECORD_GRACE = 10.0
MAX_OUTPUT_LINES = 80
MAX_BLOCKS = 3
STATE_FILE = "mosemo-agent-stop-gate.json"
INSTRUCTION = (
    "코드 스타일 검사를 통과하지 못했다. 아래 위반을 고친 뒤 다시 끝내라. "
    "저장소 검사 규칙은 사용자·Issue의 이름·형태 지시보다 우선한다. "
    "지시와 규칙이 충돌하면 요청한 기능을 규칙에 맞게 구현하고, "
    "지시와 다르게 한 점과 이유를 보고하라. "
    "규칙을 지키는 방법이 없으면 작성한 코드를 지우거나 되돌리지 말고 "
    "그대로 둔 채 원인과 추천 대응을 보고하라. "
    "위반을 없애려고 요청한 기능을 빼지 않는다."
)
SCRIPTS = ".codex/skills/verification/scripts"
STYLE_CHECKS = (
    ("ruff check", ("ruff", "check", "--output-format", "concise", ".")),
    (
        "complexipy",
        (
            "complexipy",
            "src",
            "--max-complexity-allowed",
            "15",
            "--isolated",
            "--no-ignore",
        ),
    ),
    ("validate_conventions", ("python", f"{SCRIPTS}/validate_conventions.py")),
    ("validate_architecture", ("python", f"{SCRIPTS}/validate_architecture.py")),
    ("ty check", ("ty", "check", "--output-format", "concise")),
    ("lint-imports", ("poe", "lint-imports")),
)
PYTEST = (
    "--env-file",
    ".env.example",
    "pytest",
    "-q",
    "--tb=short",
    "-p",
    "no:cacheprovider",
    "-m",
    "not requires_db and not requires_kafka",
    "tests/unit",
)
INTEGRATION_TESTS = "tests/integration"
# Packages every module depends on; a change selects all integration tests.
SHARED_PACKAGES = {"shared", "platform", "entrypoints"}
LOCATION = re.compile(r"(\S+?):\d+(?::\d+)?")
PYTEST_PROBLEM = re.compile(r"^(?:FAILED|ERROR) (\S+)")


@dataclass(frozen=True)
class Failure:
    """실패한 검사 하나. `items`는 실행마다 달라지는 줄·열 번호와 시간을 뺀 항목이다."""

    name: str
    command: str
    output: str
    items: frozenset[str]
    broken: bool = False

    def report(self) -> str:
        lines = self.output.rstrip().splitlines()
        shown = lines[:MAX_OUTPUT_LINES]
        if len(lines) > MAX_OUTPUT_LINES:
            shown.append(f"... {len(lines) - MAX_OUTPUT_LINES}줄 생략")
        return "\n".join([f"$ {self.command}", *shown])


@dataclass(frozen=True)
class Outcome:
    """Stop 판정. `result`는 지표 로그의 결과 값이다."""

    code: int
    result: str
    failures: tuple[Failure, ...] = ()


def broken_check(name: str, *, command: str, error: Exception) -> Failure:
    """hook 자체 실패를 분류(예외, 명령 없음, 시간 초과)와 검사 이름으로 나타낸다."""
    if isinstance(error, FileNotFoundError):
        kind, reason = "명령 없음", f"{error.filename or error}을 찾을 수 없다"
    elif isinstance(error, subprocess.TimeoutExpired):
        kind, reason = "시간 초과", f"내부 제한 {TIME_LIMIT:g}초를 넘었다"
    else:
        kind, reason = "예외", f"{type(error).__name__}: {error}"
    return Failure(
        name=name,
        command=command,
        output=f"검사기 실행 실패: {name} {kind}: {reason}",
        items=frozenset({kind}),
        broken=True,
    )


def run(
    command: list[str], *, root: Path, deadline: float
) -> subprocess.CompletedProcess[str]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(command, 0)
    return subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=remaining,
        check=False,
    )


def git(arguments: list[str], *, root: Path, deadline: float) -> str:
    completed = run(["git", *arguments], root=root, deadline=deadline)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode, completed.args, completed.stdout, completed.stderr
        )
    return completed.stdout


def changed_paths(root: Path, deadline: float) -> list[str]:
    """`git status`의 바뀐 경로. 이름 변경은 새 경로와 옛 경로를 모두 넣는다."""
    fields = git(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        root=root,
        deadline=deadline,
    ).split("\0")
    paths = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        paths.append(entry[3:])
        status = entry[:2]
        if "R" in status or "C" in status:
            # -z 출력에서 이름 변경·복사의 원래 경로는 다음 필드다.
            if "R" in status:
                paths.append(fields[index])
            index += 1
    return sorted(set(paths))


def change_fingerprint(paths: list[str], root: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        file = root / path
        content = (
            hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else "-"
        )
        digest.update(f"{path}\0{content}\0".encode())
    return digest.hexdigest()


def integration_tests(paths: list[str], root: Path) -> list[str]:
    """바뀐 `.py` 경로(삭제 포함)로 함께 실행할 integration 테스트 경로를 고른다."""
    selected = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        if parts[:2] == ("src", "mosemo"):
            if len(parts) == 3 or parts[2] in SHARED_PACKAGES:
                selected.add(INTEGRATION_TESTS)
            else:
                selected.add(f"{INTEGRATION_TESTS}/{parts[2]}")
        elif parts[0] == "tests" and (len(parts) == 2 or parts[1] == "support"):
            selected.add(INTEGRATION_TESTS)
        elif parts[:2] == ("tests", "integration"):
            selected.add(path if parts[-1].startswith("test_") else INTEGRATION_TESTS)
    if INTEGRATION_TESTS in selected:
        selected = {INTEGRATION_TESTS}
    return sorted(test for test in selected if (root / test).exists())


def complexipy_failures(output: str) -> str:
    """통과한 함수 줄을 지우고, 실패한 함수와 그 파일 이름만 남긴다."""
    kept = []
    current_file = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.endswith("PASSED"):
            continue
        if stripped.endswith("FAILED"):
            if current_file is not None:
                kept.append(current_file)
                current_file = None
            kept.append(line)
        elif not line.startswith(" ") and stripped.endswith(".py"):
            current_file = line
        else:
            kept.append(line)
    return "\n".join(kept)


def failure_items(name: str, output: str) -> frozenset[str]:
    if name == "pytest":
        return frozenset(
            match.group(1)
            for line in output.splitlines()
            if (match := PYTEST_PROBLEM.match(line))
        )
    return frozenset(
        normalized
        for line in output.splitlines()
        if (normalized := LOCATION.sub(r"\1", line).strip())
    )


def run_check(
    name: str, arguments: list[str], *, root: Path, deadline: float
) -> Failure | None:
    command = [*UV_RUN, *arguments]
    shown = shlex.join(command)
    try:
        completed = run(command, root=root, deadline=deadline)
    except (OSError, subprocess.SubprocessError) as error:
        return broken_check(name, command=shown, error=error)
    if completed.returncode == 0:
        return None
    output = completed.stdout + completed.stderr
    if name == "complexipy":
        output = complexipy_failures(output)
    return Failure(
        name=name, command=shown, output=output, items=failure_items(name, output)
    )


def style_failures(
    python_paths: list[str], *, root: Path, deadline: float
) -> list[Failure]:
    failures = []
    for name, arguments in STYLE_CHECKS:
        failure = run_check(name, list(arguments), root=root, deadline=deadline)
        if failure is not None:
            failures.append(failure)
            if failure.broken:
                break
    if failures:
        return failures
    tests = [*PYTEST, *integration_tests(python_paths, root)]
    failure = run_check("pytest", tests, root=root, deadline=deadline)
    return [failure] if failure is not None else []


def load_state(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def session_record(state: dict[str, Any], session: str) -> dict[str, Any]:
    record = state.get(session)
    return record if isinstance(record, dict) else {}


def allow(
    state_path: Path | None, session: str, *, passed_change: str | None = None
) -> int:
    """통과시킨다. 차단 기록은 지우고, 검사 통과일 때만 통과 지문을 남긴다."""
    state = load_state(state_path)
    record = session_record(state, session)
    record.pop("blocked", None)
    if passed_change is not None:
        record["passed"] = passed_change
    state[session] = record
    save_state(state_path, state)
    return 0


def failure_fingerprint(failures: list[Failure]) -> str:
    summary = sorted([failure.name, sorted(failure.items)] for failure in failures)
    return hashlib.sha256(json.dumps(summary, ensure_ascii=False).encode()).hexdigest()


def block(
    state_path: Path | None,
    session: str,
    *,
    failures: list[Failure],
    change: str | None,
) -> int:
    """같은 실패로 이미 3번 막았으면 통과시키고, 아니면 기록하고 막는다."""
    state = load_state(state_path)
    record = session_record(state, session)
    previous = record.get("blocked")
    fingerprint = failure_fingerprint(failures)
    count = 1
    if isinstance(previous, dict) and previous.get("failure") == fingerprint:
        count = int(previous.get("count", 0)) + 1
    if count > MAX_BLOCKS:
        return allow(state_path, session)
    record["blocked"] = {"failure": fingerprint, "change": change, "count": count}
    blocks = record.get("blocks")
    record["blocks"] = (blocks if isinstance(blocks, int) else 0) + 1
    state[session] = record
    save_state(state_path, state)
    print(
        "\n\n".join([INSTRUCTION, *(failure.report() for failure in failures)]),
        file=sys.stderr,
    )
    return 2


def block_outcome(
    state_path: Path | None,
    session: str,
    *,
    failures: list[Failure],
    change: str | None,
) -> Outcome:
    code = block(state_path, session, failures=failures, change=change)
    if code == 0:
        result = "capped"
    elif any(failure.broken for failure in failures):
        result = "error"
    else:
        result = "block"
    return Outcome(code, result, tuple(failures))


def gate(root: Path, *, state_path: Path, session: str, deadline: float) -> Outcome:
    python_paths = [
        path for path in changed_paths(root, deadline) if path.endswith(".py")
    ]
    if not python_paths:
        return Outcome(allow(state_path, session), "skip")
    failures = []
    existing = [path for path in python_paths if (root / path).is_file()]
    if existing:
        formatting = run_check(
            "ruff format", ["ruff", "format", *existing], root=root, deadline=deadline
        )
        failures += [formatting] if formatting is not None else []
    record = session_record(load_state(state_path), session)
    blocked = record.get("blocked")
    known = {
        record.get("passed"),
        blocked.get("change") if isinstance(blocked, dict) else None,
    }
    if change_fingerprint(changed_paths(root, deadline), root) in known:
        return Outcome(allow(state_path, session), "cached")
    if not any(failure.broken for failure in failures):
        failures += style_failures(python_paths, root=root, deadline=deadline)
    # 포맷과 complexipy 기준선 갱신처럼 hook이 쓴 파일을 반영한 지문을 기록한다.
    # 검사가 내부 제한을 다 써도 기록할 수 있게 여유 시간을 둔다.
    change = change_fingerprint(changed_paths(root, deadline + RECORD_GRACE), root)
    if not failures:
        return Outcome(allow(state_path, session, passed_change=change), "allow")
    return block_outcome(state_path, session, failures=failures, change=change)


def git_query(root: Path | None, deadline: float) -> stop_metrics.Git:
    """지표 기록용 git 조회. 실패는 `OSError`로 알린다."""

    def query(arguments: list[str]) -> str:
        if root is None:
            raise OSError("git 저장소를 찾지 못했다")
        try:
            return git(arguments, root=root, deadline=deadline)
        except subprocess.SubprocessError as error:
            raise OSError(error) from error

    return query


def payload_session(payload: Any) -> str:
    return str(payload.get("session_id", "")) if isinstance(payload, dict) else ""


def metrics_fields(
    payload: Any,
    *,
    outcome: Outcome,
    git: dict[str, Any],
    blocks: int,
) -> dict[str, Any]:
    agent = os.environ.get("MOSEMO_AGENT")
    skills, model = stop_metrics.read_session(payload, agent=agent)
    source = stop_metrics.Source(
        session=payload_session(payload), agent=agent, model=model
    )
    return {
        **stop_metrics.common_fields("stop", source=source, git=git),
        "skills": skills,
        "result": outcome.result,
        "session_blocks": blocks,
        "failed": [failure.name for failure in outcome.failures],
        "rules": stop_metrics.rule_counts(
            (failure.name, failure.output) for failure in outcome.failures
        ),
    }


def record_metrics(
    payload: Any, *, outcome: Outcome, state_path: Path | None, git: stop_metrics.Git
) -> None:
    """Stop 줄을 남긴다. 실패해도 Stop 판정은 바꾸지 않는다."""

    def build() -> dict[str, Any]:
        record = session_record(load_state(state_path), payload_session(payload))
        blocks = record.get("blocks", 0)
        return metrics_fields(
            payload,
            outcome=outcome,
            git=stop_metrics.query_git_facts(git),
            blocks=blocks if isinstance(blocks, int) else 0,
        )

    stop_metrics.record(build, git=git)


def main() -> int:
    deadline = time.monotonic() + TIME_LIMIT
    root = None
    state_path = None
    payload: Any = None
    try:
        root = Path(
            git(
                ["rev-parse", "--show-toplevel"], root=Path.cwd(), deadline=deadline
            ).strip()
        )
        git_dir = git(["rev-parse", "--git-dir"], root=root, deadline=deadline)
        state_path = root / git_dir.strip() / STATE_FILE
        payload = json.load(sys.stdin)
        session = str(payload.get("session_id", ""))
        outcome = gate(root, state_path=state_path, session=session, deadline=deadline)
    # 종료 전 hook의 실패는 통과시키지 않고 막는다.
    except (
        OSError,
        ValueError,
        LookupError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ) as error:
        failure = broken_check("stop_gate", command="stop_gate.py", error=error)
        outcome = block_outcome(
            state_path, payload_session(payload), failures=[failure], change=None
        )
    record_metrics(
        payload,
        outcome=outcome,
        state_path=state_path,
        git=git_query(root, time.monotonic() + RECORD_GRACE),
    )
    # `--agent`로 실행한 리뷰 에이전트 세션은 통과한 Stop마다 리뷰 줄을 하나 더 남긴다.
    review_metrics.record_review(
        payload,
        code=outcome.code,
        git=git_query(root, time.monotonic() + RECORD_GRACE),
    )
    return outcome.code


if __name__ == "__main__":
    sys.exit(main())
