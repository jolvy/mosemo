"""리뷰 지표(`review_metrics.py`)와 리뷰 에이전트 첫 줄 지시문을 검사한다.

hook의 `main`, git, subprocess는 부르지 않는다(Plan #109 S7).
"""

import json
import re
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

GIT = {"harness_rev": "abc1234", "harness_dirty": False, "branch": "feat/x"}
COMMON_KEYS = [
    "event",
    "session",
    "agent",
    "model",
    "harness_rev",
    "harness_dirty",
    "branch",
]
REVIEWERS = ("code-reviewer", "domain-reviewer", "security-auditor")
REPORT_FORMAT = "Report format (identical for every reviewer):"


def assistant(
    text: str,
    *,
    identifier: str = "m1",
    block: str = "text",
    model: str = "claude-opus-5-5",
) -> str:
    content = [{"type": block, block: text}]
    message = {"id": identifier, "model": model, "content": content}
    return json.dumps({"type": "assistant", "message": message})


def subagent_stop(agent_type: str, report: str) -> dict[str, Any]:
    return {
        "session_id": "s1",
        "hook_event_name": "SubagentStop",
        "agent_type": agent_type,
        "last_assistant_message": report,
    }


def line(
    review_metrics: ModuleType,
    payload: dict[str, Any],
    *,
    transcript: list[str] | None = None,
    code: int | None = None,
) -> dict[str, Any] | None:
    return review_metrics.review_line(
        payload, agent="claude", git=GIT, transcript=transcript or [], code=code
    )


def assert_common(fields: dict[str, Any]) -> None:
    assert list(fields)[: len(COMMON_KEYS)] == COMMON_KEYS
    assert fields["event"] == "review"
    assert isinstance(fields["harness_dirty"], bool)


@pytest.mark.parametrize(
    ("report", "findings"),
    [
        (
            "REVIEW high=1 medium=2 low=0\n\n## 지적\n...",
            {"high": 1, "medium": 2, "low": 0},
        ),
        ("## 리뷰 결과\nREVIEW high=1 medium=2 low=0", None),
        ("REVIEW high=0 medium=0 low=0", {"high": 0, "medium": 0, "low": 0}),
    ],
)
def test_subagent_stop_review_line_parses_first_line(
    review_metrics: ModuleType, report: str, findings: dict[str, int] | None
) -> None:
    fields = line(review_metrics, subagent_stop("code-reviewer", report))

    assert fields is not None
    assert_common(fields)
    assert fields["reviewer"] == "code-reviewer"
    assert fields["findings"] == findings
    assert fields["format_ok"] is (findings is not None)
    assert fields["via"] == "subagent_stop"
    assert list(fields)[len(COMMON_KEYS) :] == [
        "reviewer",
        "findings",
        "format_ok",
        "via",
    ]


def test_subagent_stop_of_other_agent_writes_nothing(
    review_metrics: ModuleType,
) -> None:
    payload = subagent_stop("Explore", "REVIEW high=0 medium=0 low=0")

    assert line(review_metrics, payload) is None


@pytest.mark.parametrize(
    "report",
    [
        "  \n  REVIEW high=3 medium=0 low=12  \n본문",
        "REVIEW high=3 medium=0 low=12",
    ],
)
def test_parse_report_strips_blank_lines_and_spaces(
    review_metrics: ModuleType, report: str
) -> None:
    assert review_metrics.parse_report(report) == {"high": 3, "medium": 0, "low": 12}


@pytest.mark.parametrize(
    "report",
    ["", "REVIEW high=1 medium=2", "**REVIEW high=1 medium=2 low=0**", "review"],
)
def test_parse_report_rejects_other_first_lines(
    review_metrics: ModuleType, report: str
) -> None:
    assert review_metrics.parse_report(report) is None


def agent_stop(message: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": "s2",
        "hook_event_name": "Stop",
        "agent_type": "security-auditor",
    }
    if message is not None:
        payload["last_assistant_message"] = message
    return payload


TRANSCRIPT = [
    assistant("REVIEW high=9 medium=9 low=9", identifier="m0"),
    "not json",
    assistant("생각", identifier="m1", block="thinking"),
    assistant("REVIEW high=0 medium=1 low=2", identifier="m1"),
    assistant("\n본문", identifier="m1"),
    '{"type":"user","message":{"content":"다음"}}',
]


@pytest.mark.parametrize(
    ("message", "findings"),
    [
        ("REVIEW high=2 medium=0 low=0", {"high": 2, "medium": 0, "low": 0}),
        (None, {"high": 0, "medium": 1, "low": 2}),
    ],
)
def test_agent_stop_review_line_after_passing_stop(
    review_metrics: ModuleType, message: str | None, findings: dict[str, int]
) -> None:
    fields = line(review_metrics, agent_stop(message), transcript=TRANSCRIPT, code=0)

    assert fields is not None
    assert_common(fields)
    assert fields["session"] == "s2"
    assert fields["model"] == "claude-opus-5-5"
    assert fields["reviewer"] == "security-auditor"
    assert fields["findings"] == findings
    assert fields["format_ok"] is True
    assert fields["via"] == "agent_stop"


@pytest.mark.parametrize("message", ["REVIEW high=2 medium=0 low=0", None])
def test_agent_stop_review_line_skips_blocked_stop(
    review_metrics: ModuleType, message: str | None
) -> None:
    payload = agent_stop(message)

    assert line(review_metrics, payload, transcript=TRANSCRIPT, code=2) is None


def test_last_assistant_text_starts_new_message_on_new_id(
    review_metrics: ModuleType,
) -> None:
    lines = [*TRANSCRIPT, assistant("마지막", identifier="m2")]

    assert review_metrics.last_assistant_text(lines) == "마지막"
    assert review_metrics.last_assistant_text([]) == ""


def test_record_review_writes_line_from_agent_transcript(
    review_metrics: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "m.jsonl"
    transcript = tmp_path / "agent.jsonl"
    transcript.write_text("\n".join(TRANSCRIPT) + "\n", encoding="utf-8")
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(log))
    monkeypatch.setenv("MOSEMO_AGENT", "claude")
    payload = {
        "session_id": "s1",
        "agent_type": "domain-reviewer",
        "agent_transcript_path": str(transcript),
    }

    review_metrics.record_review(payload, code=None, git=failing_git)

    fields = json.loads(log.read_text(encoding="utf-8"))
    assert fields["event"] == "review"
    assert fields["agent"] == "claude"
    assert fields["harness_dirty"] is True
    assert fields["findings"] == {"high": 0, "medium": 1, "low": 2}
    assert fields["via"] == "subagent_stop"


def failing_git(arguments: list[str]) -> str:
    raise OSError(arguments[0])


@pytest.mark.parametrize(("code", "written"), [(0, True), (2, False)])
def test_record_review_reads_session_transcript_after_agent_stop(
    review_metrics: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    written: bool,
) -> None:
    log = tmp_path / "m.jsonl"
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("\n".join(TRANSCRIPT) + "\n", encoding="utf-8")
    other = tmp_path / "other.jsonl"
    other_line = assistant("REVIEW high=7 medium=7 low=7", model="claude-haiku-5-5")
    other.write_text(other_line + "\n", encoding="utf-8")
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(log))
    payload = {
        **agent_stop(None),
        "transcript_path": str(transcript),
        "agent_transcript_path": str(other),
    }

    review_metrics.record_review(payload, code=code, git=failing_git)

    assert log.exists() is written
    if written:
        fields = json.loads(log.read_text(encoding="utf-8"))
        assert fields["via"] == "agent_stop"
        assert fields["model"] == "claude-opus-5-5"
        assert fields["findings"] == {"high": 0, "medium": 1, "low": 2}


@pytest.mark.parametrize(
    "report",
    [
        "  \n  REVIEW high=3 medium=0 low=12  \n본문",
        "REVIEW high=0 medium=0 low=0",
        "## 리뷰 결과\nREVIEW high=1 medium=2 low=0",
        "**REVIEW high=1 medium=2 low=0**",
        "REVIEW high=1 medium=2",
        "",
    ],
)
def test_parse_report_matches_report_aggregation(
    review_metrics: ModuleType, report_aggregate: ModuleType, report: str
) -> None:
    assert review_metrics.parse_report(report) == report_aggregate.review_findings(
        report
    )


def test_first_line_rule_matches_report_aggregation(
    review_metrics: ModuleType, report_aggregate: ModuleType
) -> None:
    assert review_metrics.FIRST_LINE.pattern == report_aggregate.REVIEW_LINE.pattern
    assert review_metrics.REVIEWERS == report_aggregate.REVIEW_AGENTS


def test_record_review_skips_other_agent_without_git(
    review_metrics: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(tmp_path / "m.jsonl"))

    def unexpected_git(arguments: list[str]) -> str:
        raise AssertionError(arguments)

    review_metrics.record_review(
        {"agent_type": "Explore"}, code=None, git=unexpected_git
    )

    assert not (tmp_path / "m.jsonl").exists()


@pytest.mark.parametrize("agent_type", [["code-reviewer"], {"name": "x"}, None])
def test_non_string_agent_type_is_not_a_review(
    review_metrics: ModuleType, agent_type: object
) -> None:
    payload = {"session_id": "s1", "agent_type": agent_type}

    def unexpected_git(arguments: list[str]) -> str:
        raise AssertionError(arguments)

    assert line(review_metrics, payload) is None
    review_metrics.record_review(payload, code=0, git=unexpected_git)


def test_record_review_failure_prints_one_stderr_line(
    review_metrics: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(tmp_path / "missing" / "m.jsonl"))
    payload = subagent_stop("code-reviewer", "REVIEW high=0 medium=0 low=0")

    review_metrics.record_review(payload, code=None, git=failing_git)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert captured.err.startswith("지표 로그 기록 실패")


def report_format(repository: Path, name: str) -> list[str]:
    """toml `developer_instructions`의 리뷰 보고 형식 절(빈 줄 전까지)."""
    path = repository / ".codex/agents" / f"{name}.toml"
    text = tomllib.loads(path.read_text(encoding="utf-8"))["developer_instructions"]
    lines = text.splitlines()
    assert REPORT_FORMAT in lines, f"{path}: 리뷰 보고 형식 절이 없다"
    start = lines.index(REPORT_FORMAT)
    end = lines.index("", start)
    return lines[start:end]


def test_reviewers_share_report_format_instructions(repository: Path) -> None:
    sections = {name: report_format(repository, name) for name in REVIEWERS}
    section = sections["code-reviewer"]

    assert all(other == section for other in sections.values())
    assert any(
        re.search(r"first line .* is exactly `REVIEW high=N medium=N low=N`", line)
        for line in section
    )
    assert any(line.startswith("- high: a demonstrated defect") for line in section)
    assert any(line.startswith("- medium: ") for line in section)
    assert any(line.startswith("- low: ") for line in section)
    assert any(
        "save the result to a file" in line and "final response" in line
        for line in section
    )


def test_claude_settings_register_review_metrics_on_subagent_stop(
    repository: Path,
) -> None:
    settings = json.loads(
        (repository / ".claude/settings.json").read_text(encoding="utf-8")
    )
    hooks = settings["hooks"]
    review_hooks = [
        hook
        for entry in hooks["SubagentStop"]
        for hook in entry["hooks"]
        if "review_metrics.py" in hook.get("command", "")
    ]

    assert review_hooks == [
        {
            "type": "command",
            "command": 'cd "$CLAUDE_PROJECT_DIR" && MOSEMO_AGENT=claude '
            "uv run --locked python .codex/hooks/review_metrics.py",
            "timeout": 10,
        }
    ]
    assert hooks["PostToolUse"] == [
        {
            "matcher": "Edit|Write",
            "hooks": [
                {
                    "type": "command",
                    "command": 'cd "$CLAUDE_PROJECT_DIR" && MOSEMO_AGENT=claude '
                    "uv run --locked python .codex/hooks/post_edit_feedback.py",
                    "timeout": 10,
                }
            ],
        }
    ]
    assert hooks["Stop"] == [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": 'cd "$CLAUDE_PROJECT_DIR" && MOSEMO_AGENT=claude '
                    "uv run --locked python .codex/hooks/stop_gate.py",
                    "timeout": 300,
                }
            ]
        }
    ]
