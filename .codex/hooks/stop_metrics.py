"""hook의 지표 로그: 세션 기록·검사 출력 파서, 공통 필드 생성과 기록 함수.

git과 subprocess를 직접 부르지 않는다. git 조회는 hook이 넘기는 함수(`Git`)로 한다.
기록 실패는 stderr에 한 줄만 남기고 반환한다.
"""

import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKILL_FILE = re.compile(r"(?:^|/)\.(?:codex|claude)/skills/([^/]+)/SKILL\.md$")
COMMAND_NAME = re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>")
# 규칙 코드, 또는 규칙 코드가 없는 구문 오류의 표시(`invalid-syntax`, 옛 `SyntaxError`).
RUFF_CODE = re.compile(r"^.+?:\d+:\d+: ([A-Z]+[0-9]+\b|invalid-syntax|SyntaxError)")
CONVENTION_CODE = re.compile(r"\b(CS-P\d+)\b")
PYTEST_PROBLEM = re.compile(r"^(?:FAILED|ERROR) \S+")
SUMMARY = re.compile(r"^Found \d+ ")
CODED_CHECKS = {"ruff check": RUFF_CODE, "validate_conventions": CONVENTION_CODE}
METRICS_FILE = "mosemo-agent-metrics.jsonl"
HARNESS_PATHS = (
    ".codex/",
    ".claude/",
    "AGENTS.md",
    "CLAUDE.md",
    "src/mosemo/AGENTS.md",
    "src/mosemo/CLAUDE.md",
    "tests/AGENTS.md",
    "tests/CLAUDE.md",
    "tests/unit/harness/",
    "docs/code-style-rubric.md",
    "docs/adr/",
)
HARNESS_LOG = ("log", "-1", "--format=%h", "--", *HARNESS_PATHS)
HARNESS_STATUS = ("status", "--porcelain", "--", *HARNESS_PATHS)
BRANCH = ("branch", "--show-current")
COMMON_DIR = ("rev-parse", "--path-format=absolute", "--git-common-dir")
# 편집 hook은 편집마다 실행되므로 세션 기록의 끝부분만 읽는다.
TAIL_BYTES = 1024 * 1024
# 기록 함수가 삼키는 실패. git 조회 함수는 실패를 `OSError`로 알린다.
RECORD_FAILURES = (OSError, ValueError, LookupError, TypeError, AttributeError)

type Git = Callable[[list[str]], str]


@dataclass(frozen=True)
class Source:
    """줄을 남기는 세션: hook 입력 `session_id`, `MOSEMO_AGENT` 값, 모델."""

    session: str = ""
    agent: str | None = None
    model: str | None = None


def walk(value: Any) -> Iterator[dict[str, Any]]:
    """JSON 값 안의 모든 객체를 바깥부터 순서대로 내놓는다."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def tool_skill(node: dict[str, Any]) -> str | None:
    if node.get("type") != "tool_use" or not isinstance(node.get("input"), dict):
        return None
    tool_input = node["input"]
    if node.get("name") == "Skill":
        skill = tool_input.get("skill")
        return skill if isinstance(skill, str) and skill else None
    if node.get("name") == "Read":
        match = SKILL_FILE.search(str(tool_input.get("file_path", "")))
        return match.group(1) if match else None
    return None


def user_commands(entry: Any) -> list[str]:
    """사용자 메시지 글 안의 `<command-name>` 값. 앞의 `/`는 빼고 도구 결과는 보지 않는다."""
    if not isinstance(entry, dict) or entry.get("type") != "user":
        return []
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    blocks = (
        [{"type": "text", "text": content}] if isinstance(content, str) else content
    )
    texts = [
        str(block.get("text", ""))
        for block in (blocks if isinstance(blocks, list) else [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return [name for text in texts for name in COMMAND_NAME.findall(text)]


def transcript_facts(lines: Iterable[str]) -> tuple[list[str], str | None]:
    """세션 기록 줄에서 Skill 이름(중복 제거, 나온 순서)과 마지막 Claude 모델을 찾는다."""
    skills: dict[str, None] = {}
    model = None
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        found = user_commands(entry)
        for node in walk(entry):
            found += [skill] if (skill := tool_skill(node)) else []
            value = node.get("model")
            if isinstance(value, str) and value.startswith("claude-"):
                model = value
        skills.update(dict.fromkeys(found))
    return list(skills), model


def read_transcript(path: str | None) -> tuple[list[str], str | None]:
    """hook 입력의 `transcript_path`를 읽는다. 없거나 읽을 수 없으면 빈 결과다."""
    if not path:
        return [], None
    try:
        with Path(path).open(encoding="utf-8", errors="replace") as file:
            return transcript_facts(file)
    except OSError:
        return [], None


def read_transcript_model(path: str | None, *, size: int = TAIL_BYTES) -> str | None:
    """세션 기록 끝 `size` 바이트에서 마지막 Claude 모델을 찾는다. 모르면 `None`이다."""
    if not path:
        return None
    try:
        with Path(path).open("rb") as file:
            # 끝부분 바로 앞 1바이트도 읽어 첫 줄이 잘렸는지 판단한다.
            offset = max(file.seek(0, os.SEEK_END) - size - 1, 0)
            file.seek(offset)
            lines = file.read(size + 1).split(b"\n")
    except OSError:
        return None
    # 중간에서 읽기 시작했으면 첫 조각은 앞 줄의 끝이거나 잘린 줄이다.
    # JSON 문자열 안에 그대로 올 수 있는 U+2028 등에서 줄을 나누지 않도록 `\n`으로만 나눈다.
    kept = lines[1:] if offset else lines
    return transcript_facts(line.decode(errors="replace") for line in kept)[1]


def failure_lines(name: str, output: str) -> list[str]:
    """규칙 ID가 없는 검사의 실패 줄."""
    lines = [line for line in output.splitlines() if line.strip()]
    if name == "pytest":
        return [line for line in lines if PYTEST_PROBLEM.match(line)]
    if name == "complexipy":
        return [line for line in lines if line.strip().endswith("FAILED")]
    return [line for line in lines if not SUMMARY.match(line)]


def rule_counts(failures: Iterable[tuple[str, str]]) -> dict[str, int]:
    """(검사 이름, 출력) 목록에서 규칙별 위반 수를 센다.

    Ruff와 `validate_conventions`는 규칙 코드를, 그 밖의 검사는 검사 이름을 키로 쓴다.
    """
    counts: Counter[str] = Counter()
    for name, output in failures:
        pattern = CODED_CHECKS.get(name)
        codes = []
        if pattern is not None:
            codes = [
                match.group(1)
                for line in output.splitlines()
                if (match := pattern.search(line))
            ]
        if codes:
            counts.update(codes)
        elif lines := failure_lines(name, output):
            counts[name] += len(lines)
    return dict(counts)


def ruff_rules(output: str) -> dict[str, int]:
    """Ruff concise 출력의 규칙 ID별 건수."""
    return dict(
        Counter(
            match.group(1)
            for line in output.splitlines()
            if (match := RUFF_CODE.search(line))
        )
    )


def git_facts(
    *, log: str | None, status: str | None, branch: str | None
) -> dict[str, Any]:
    """git 출력을 공통 필드 값으로 바꾼다. `None`은 조회 실패(모름)다.

    하네스 변경 여부를 모르면 커밋 안 된 변경이 있는 쪽(`true`)으로 둔다.
    """
    return {
        "harness_rev": (log or "").strip() or None,
        "harness_dirty": status is None or bool(status.strip()),
        "branch": (branch or "").strip() or None,
    }


def ask(git: Git, arguments: tuple[str, ...]) -> str | None:
    """git 출력. 조회 실패는 `None`이다."""
    try:
        return git(list(arguments))
    except OSError:
        return None


def query_git_facts(git: Git) -> dict[str, Any]:
    return git_facts(
        log=ask(git, HARNESS_LOG),
        status=ask(git, HARNESS_STATUS),
        branch=ask(git, BRANCH),
    )


def common_fields(
    event: str, *, source: Source, git: Mapping[str, Any]
) -> dict[str, Any]:
    """모든 줄의 공통 필드(`at` 제외, 기록 순서대로)."""
    return {
        "event": event,
        "session": source.session,
        "agent": source.agent or "unknown",
        "model": source.model,
        "harness_rev": git["harness_rev"],
        "harness_dirty": git["harness_dirty"],
        "branch": git["branch"],
    }


def metrics_line(fields: dict[str, Any], *, at: time.struct_time) -> str:
    """UTC 시각 `at`을 앞에 둔 JSON 한 줄(줄바꿈 포함)."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", at)
    return json.dumps({"at": stamp, **fields}, ensure_ascii=False) + "\n"


def append_metrics(path: Path, line: str) -> None:
    """한 줄을 append 모드에서 한 번의 `write`로 추가한다. 실패는 stderr 한 줄로 끝낸다."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(descriptor, line.encode(errors="replace"))
        finally:
            os.close(descriptor)
    except OSError as error:
        print(f"지표 로그 기록 실패: {path}: {error}", file=sys.stderr)


def record(build: Callable[[], dict[str, Any] | None], *, git: Git) -> None:
    """`build`가 만든 줄을 지표 로그에 붙인다. `None`이면 쓰지 않는다.

    경로는 `MOSEMO_METRICS_LOG`, 없으면 공통 git 디렉터리 아래다. 줄을 만드는 조회가
    마감을 다 써도 줄이 남도록 경로를 먼저 찾는다. 실패는 예외를 내지 않고 stderr에
    `지표 로그 기록 실패`로 시작하는 한 줄만 남긴다.
    """
    try:
        path = os.environ.get("MOSEMO_METRICS_LOG")
        if not path:
            common = (ask(git, COMMON_DIR) or "").strip()
            path = str(Path(common) / METRICS_FILE) if common else None
        if not path:
            print(
                "지표 로그 기록 실패: 공통 git 디렉터리를 찾지 못했다", file=sys.stderr
            )
            return
        fields = build()
        if fields is not None:
            append_metrics(Path(path), metrics_line(fields, at=time.gmtime()))
    except RECORD_FAILURES as error:
        print(f"지표 로그 기록 실패: {type(error).__name__}: {error}", file=sys.stderr)
