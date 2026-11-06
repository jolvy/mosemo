"""종료 전 hook의 지표 로그: 세션 기록·검사 출력 파서와 기록 함수.

git과 subprocess를 쓰지 않는다. 기록 실패는 stderr에 한 줄만 남기고 반환한다.
"""

import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator
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
