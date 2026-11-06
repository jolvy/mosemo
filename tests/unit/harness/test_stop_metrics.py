import json
import time
from pathlib import Path
from types import ModuleType

import pytest

SKILL_CALL = (
    '{"type":"assistant","message":{"model":"claude-opus-5-5","content":'
    '[{"type":"tool_use","id":"toolu_1","name":"Skill",'
    '"input":{"skill":"engineering-planning","args":"#186"}}]}}'
)
SLASH_COMMAND = (
    '{"type":"user","message":{"role":"user","content":'
    '"<command-name>/verification</command-name>\\n'
    '<command-message>verification</command-message>"}}'
)
SKILL_READ = (
    '{"type":"assistant","message":{"model":"claude-sonnet-5-5","content":'
    '[{"type":"tool_use","id":"toolu_2","name":"Read","input":'
    '{"file_path":"/work/repo/.codex/skills/implementation/SKILL.md"}}]}}'
)
CLAUDE_SKILL_READ = (
    '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_3",'
    '"name":"Read","input":{"file_path":".claude/skills/specification/SKILL.md"}}]}}'
)
TOOL_RESULT = (
    '{"type":"user","message":{"role":"user","content":[{"type":"tool_result",'
    '"tool_use_id":"toolu_4","content":"<command-name>/leaked</command-name>"}]}}'
)


def test_transcript_facts_find_skills_in_order_from_three_sources(
    stop_metrics: ModuleType,
) -> None:
    lines = [SKILL_CALL, SLASH_COMMAND, SKILL_READ, CLAUDE_SKILL_READ, SKILL_CALL]

    skills, model = stop_metrics.transcript_facts(lines)

    assert skills == [
        "engineering-planning",
        "verification",
        "implementation",
        "specification",
    ]
    assert model == "claude-opus-5-5"


def test_transcript_facts_keep_last_model(stop_metrics: ModuleType) -> None:
    _, model = stop_metrics.transcript_facts([SKILL_CALL, SKILL_READ])

    assert model == "claude-sonnet-5-5"


def test_transcript_facts_skip_broken_lines_and_ignore_other_reads(
    stop_metrics: ModuleType,
) -> None:
    other_read = SKILL_READ.replace("skills/implementation/SKILL.md", "README.md")
    lines = ['{"type":"assistant",', "not json", other_read, TOOL_RESULT, SKILL_CALL]

    skills, _ = stop_metrics.transcript_facts(lines)

    assert skills == ["engineering-planning"]


def test_read_transcript_returns_empty_result_without_readable_file(
    stop_metrics: ModuleType, tmp_path: Path
) -> None:
    assert stop_metrics.read_transcript(None) == ([], None)
    assert stop_metrics.read_transcript(str(tmp_path / "missing.jsonl")) == ([], None)
    assert stop_metrics.read_transcript(str(tmp_path)) == ([], None)


def test_read_transcript_reads_jsonl_file(
    stop_metrics: ModuleType, tmp_path: Path
) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(f"{SLASH_COMMAND}\n{SKILL_CALL}\n", encoding="utf-8")

    assert stop_metrics.read_transcript(str(transcript)) == (
        ["verification", "engineering-planning"],
        "claude-opus-5-5",
    )


def test_rule_counts_use_ruff_codes(stop_metrics: ModuleType) -> None:
    output = (
        "src/a.py:3:5: PLR0917 Too many positional arguments (3/2)\n"
        "src/b.py:9:1: PLR0917 Too many positional arguments (4/2)\n"
        "src/b.py:1:1: F401 [*] `os` imported but unused\n"
        "Found 3 errors.\n"
    )

    assert stop_metrics.rule_counts([("ruff check", output)]) == {
        "PLR0917": 2,
        "F401": 1,
    }


def test_rule_counts_keep_ruff_syntax_errors_next_to_codes(
    stop_metrics: ModuleType,
) -> None:
    output = (
        "src/a.py:1:7: invalid-syntax: Expected a parameter\n"
        "src/a.py:1:8: invalid-syntax: Expected `)`, found newline\n"
        "src/old.py:2:1: SyntaxError: Unexpected indentation\n"
        "src/b.py:1:1: F401 [*] `os` imported but unused\n"
        "Found 4 errors.\n"
    )

    assert stop_metrics.rule_counts([("ruff check", output)]) == {
        "invalid-syntax": 2,
        "SyntaxError": 1,
        "F401": 1,
    }


def test_rule_counts_use_convention_codes(stop_metrics: ModuleType) -> None:
    output = (
        "src/a.py:12: CS-P23: `clock=`에 lambda를 넘기지 않는다.\n"
        "pyproject.toml: CS-P22: Ruff 설정은 검사기의 허용 설정과 같아야 한다.\n"
        "src/b.py:4: CS-P23: `clock=`에 lambda를 넘기지 않는다.\n"
    )

    assert stop_metrics.rule_counts([("validate_conventions", output)]) == {
        "CS-P23": 2,
        "CS-P22": 1,
    }


def test_rule_counts_key_other_checks_by_name(stop_metrics: ModuleType) -> None:
    failures = [
        ("validate_architecture", "src/a.py:3: ADR 0013: ...\nsrc/b.py: ADR 0011\n"),
        (
            "ty check",
            "src/a.py:1:3: error[unsupported-operator] x\nFound 1 diagnostic\n",
        ),
        ("complexipy", "src/a.py\n    run 16 FAILED\n"),
        ("pytest", "tests/unit/test_a.py F\nFAILED tests/unit/test_a.py::test_x\n"),
        ("stop_gate", "검사기 실행 실패: stop_gate 예외: ValueError: x"),
    ]

    assert stop_metrics.rule_counts(failures) == {
        "validate_architecture": 2,
        "ty check": 1,
        "complexipy": 1,
        "pytest": 1,
        "stop_gate": 1,
    }


def test_rule_counts_fall_back_to_name_without_codes(stop_metrics: ModuleType) -> None:
    output = "검사기 실행 실패: ruff check 시간 초과: 내부 제한 240초를 넘었다"

    assert stop_metrics.rule_counts([("ruff check", output)]) == {"ruff check": 1}


def test_metrics_line_is_one_json_line_with_utc_time_first(
    stop_metrics: ModuleType,
) -> None:
    at = time.struct_time((2026, 11, 6, 3, 4, 5, 4, 310, 0))

    line = stop_metrics.metrics_line({"session": "s", "skills": ["구현"]}, at=at)

    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert list(json.loads(line).items()) == [
        ("at", "2026-11-06T03:04:05Z"),
        ("session", "s"),
        ("skills", ["구현"]),
    ]


def test_append_metrics_appends_lines(stop_metrics: ModuleType, tmp_path: Path) -> None:
    log = tmp_path / "m.jsonl"

    stop_metrics.append_metrics(log, "first\n")
    stop_metrics.append_metrics(log, "second\n")

    assert log.read_text(encoding="utf-8") == "first\nsecond\n"


def test_append_metrics_swallows_write_failure(
    stop_metrics: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stop_metrics.append_metrics(tmp_path / "missing" / "m.jsonl", "line\n")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "지표 로그 기록 실패" in captured.err


def test_append_metrics_replaces_unencodable_text(
    stop_metrics: ModuleType, tmp_path: Path
) -> None:
    log = tmp_path / "m.jsonl"

    stop_metrics.append_metrics(log, "\ud800\n")

    assert log.read_bytes() == b"?\n"


def fake_git(status: str, calls: list[list[str]]) -> object:
    def git(arguments: list[str], *, root: Path, deadline: float) -> str:
        calls.append(arguments)
        return "abc1234\n" if arguments[0] == "log" else status

    return git


def test_harness_revision_ignores_environment_and_reads_git(
    stop_gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("MOSEMO_HARNESS_REV", "old")
    monkeypatch.setenv("MOSEMO_HARNESS_DIRTY", "true")
    monkeypatch.setattr(stop_gate, "git", fake_git("", calls))

    result = stop_gate.harness_revision(Path("/repo"), time.monotonic() + 10)

    assert result == ("abc1234", False)
    assert [call[0] for call in calls] == ["log", "status"]
    for call in calls:
        assert set(stop_gate.HARNESS_PATHS) <= set(call)


def test_harness_revision_reports_uncommitted_harness_changes(
    stop_gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stop_gate, "git", fake_git(" M AGENTS.md\n", []))

    _, dirty = stop_gate.harness_revision(Path("/repo"), time.monotonic() + 10)

    assert dirty is True


def test_harness_revision_without_root_ignores_environment(
    stop_gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOSEMO_HARNESS_REV", "old")
    monkeypatch.setenv("MOSEMO_HARNESS_DIRTY", "true")

    assert stop_gate.harness_revision(None, time.monotonic() + 10) == (None, None)
