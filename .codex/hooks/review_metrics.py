"""리뷰 지표: 리뷰 에이전트 보고의 첫 줄을 판정하고 지표 로그에 `review` 줄을 남긴다.

`SubagentStop` hook으로 직접 실행되고, `stop_gate.py`가 `--agent` 세션의 Stop 뒤에
`record_review`를 부른다. 언제나 exit 0이고 stdout에 쓰지 않으며, 실패는 stderr 한 줄이다.
"""

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import stop_metrics

REVIEWERS = frozenset({"code-reviewer", "domain-reviewer", "security-auditor"})
FIRST_LINE = re.compile(r"^REVIEW high=(\d+) medium=(\d+) low=(\d+)$")
# hook 설정 timeout(10초) 안에서 uv 시작 시간을 빼고 기록에 쓰는 시간.
RECORD_LIMIT = 5.0


def parse_report(report: str) -> dict[str, int] | None:
    """보고의 첫 비어 있지 않은 줄이 `REVIEW` 형식이면 심각도별 지적 수, 아니면 `None`."""
    first = next((line.strip() for line in report.splitlines() if line.strip()), "")
    match = FIRST_LINE.match(first)
    if match is None:
        return None
    high, medium, low = (int(group) for group in match.groups())
    return {"high": high, "medium": medium, "low": low}


def message_texts(entry: Any) -> tuple[str | None, list[str]] | None:
    """assistant 세션 기록 줄의 메시지 id와 `text` 블록 글. assistant 줄이 아니면 `None`."""
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return None
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    blocks = (
        [{"type": "text", "text": content}] if isinstance(content, str) else content
    )
    texts = [
        str(block.get("text", ""))
        for block in (blocks if isinstance(blocks, list) else [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    identifier = message.get("id")
    return (identifier if isinstance(identifier, str) else None), texts


def last_assistant_text(lines: Iterable[str]) -> str:
    """세션 기록의 마지막 assistant 메시지의 `text` 블록을 이은 글.

    Claude Code는 한 메시지의 블록을 같은 `message.id`의 여러 줄로 나눠 쓰므로 이어 붙인다.
    """
    current: str | None = None
    texts: list[str] = []
    for line in lines:
        try:
            found = message_texts(json.loads(line))
        except ValueError:
            continue
        if found is None:
            continue
        identifier, blocks = found
        if identifier is None or identifier != current:
            texts = []
        current = identifier
        texts += blocks
    return "\n".join(texts)


def review_report(payload: Mapping[str, Any], transcript: list[str]) -> str:
    message = payload.get("last_assistant_message")
    if isinstance(message, str) and message.strip():
        return message
    return last_assistant_text(transcript)


def is_review(payload: Any, *, code: int | None) -> bool:
    """리뷰 에이전트의 `SubagentStop`(`code`가 `None`)이거나 통과한 Stop인가.

    문자열이 아닌 `agent_type`은 집합 검사에서 `TypeError`를 내므로 먼저 거른다.
    """
    agent_type = payload.get("agent_type") if isinstance(payload, dict) else None
    return isinstance(agent_type, str) and agent_type in REVIEWERS and code in (None, 0)


def review_line(
    payload: Any,
    *,
    agent: str | None,
    git: Mapping[str, Any],
    transcript: list[str],
    code: int | None,
) -> dict[str, Any] | None:
    """리뷰 줄. `code`는 `--agent` 세션의 Stop 판정 코드이고, `None`이면 `SubagentStop`이다.

    리뷰 에이전트가 아니거나 Stop 판정이 차단(0이 아님)이면 `None`이다.
    """
    if not is_review(payload, code=code):
        return None
    findings = parse_report(review_report(payload, transcript))
    source = stop_metrics.Source(
        session=str(payload.get("session_id", "")),
        agent=agent,
        model=stop_metrics.session_facts(payload, transcript, agent=agent)[1],
    )
    return {
        **stop_metrics.common_fields("review", source=source, git=git),
        "reviewer": payload["agent_type"],
        "findings": findings,
        "format_ok": findings is not None,
        "via": "subagent_stop" if code is None else "agent_stop",
    }


def transcript_path(
    payload: Mapping[str, Any], *, code: int | None, agent: str | None
) -> str | None:
    """보고와 모델을 찾을 세션 기록. `SubagentStop`은 subagent 자신의 기록을 본다.

    입력에 경로가 없으면 Codex만 `session_id`로 세션 기록을 찾는다.
    """
    key = "agent_transcript_path" if code is None else "transcript_path"
    return stop_metrics.transcript_file(payload, agent=agent, key=key)


def record_review(payload: Any, *, code: int | None, git: stop_metrics.Git) -> None:
    """리뷰 에이전트의 보고면 리뷰 줄을 남긴다. 실패는 stderr 한 줄로 끝낸다."""
    if not is_review(payload, code=code):
        return

    def build() -> dict[str, Any] | None:
        agent = os.environ.get("MOSEMO_AGENT")
        return review_line(
            payload,
            agent=agent,
            git=stop_metrics.query_git_facts(git),
            transcript=stop_metrics.read_tail_lines(
                transcript_path(payload, code=code, agent=agent)
            ),
            code=code,
        )

    stop_metrics.record(build, git=git)


def git_query(root: Path, deadline: float) -> stop_metrics.Git:
    """지표 기록용 git 조회. 실패와 마감 초과는 `OSError`로 알린다."""

    def query(arguments: list[str]) -> str:
        try:
            return subprocess.run(
                ["git", *arguments],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=max(deadline - time.monotonic(), 0.001),
                check=True,
            ).stdout
        except subprocess.SubprocessError as error:
            raise OSError(error) from error

    return query


def main() -> int:
    # 리뷰 기록 hook은 어떤 실패에도 막지 않는다.
    try:
        payload = json.load(sys.stdin)
        deadline = time.monotonic() + RECORD_LIMIT
        record_review(payload, code=None, git=git_query(Path.cwd(), deadline))
    # 깊게 중첩된 JSON은 `RecursionError`(`RuntimeError`)를 낸다.
    except (
        OSError,
        ValueError,
        LookupError,
        TypeError,
        AttributeError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"review_metrics: {type(error).__name__}: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
