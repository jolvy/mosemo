import json
import threading
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


COMMON_KEYS = [
    "at",
    "event",
    "session",
    "agent",
    "model",
    "harness_rev",
    "harness_dirty",
    "branch",
]
GIT = {"harness_rev": "abc1234", "harness_dirty": False, "branch": "feat/x"}
AT = time.struct_time((2026, 11, 6, 3, 4, 5, 4, 310, 0))


def test_git_facts_read_git_output(stop_metrics: ModuleType) -> None:
    facts = stop_metrics.git_facts(log="abc1234\n", status="", branch="feat/x\n")

    assert facts == GIT
    assert stop_metrics.git_facts(log="a\n", status=" M AGENTS.md\n", branch="b")[
        "harness_dirty"
    ]


def test_git_facts_treat_unknown_as_dirty_without_revision_or_branch(
    stop_metrics: ModuleType,
) -> None:
    facts = stop_metrics.git_facts(log=None, status=None, branch=None)

    assert facts["harness_dirty"] is True
    assert facts["harness_rev"] is None
    assert facts["branch"] is None


def test_git_facts_treat_detached_head_as_no_branch(stop_metrics: ModuleType) -> None:
    facts = stop_metrics.git_facts(log="abc1234\n", status="", branch="\n")

    assert facts["branch"] is None


def test_common_fields_have_common_keys_in_order(stop_metrics: ModuleType) -> None:
    unknown = stop_metrics.git_facts(log=None, status=None, branch=None)
    fields = stop_metrics.common_fields(
        "edit", source=stop_metrics.Source(), git=unknown
    )

    line = json.loads(stop_metrics.metrics_line(fields, at=AT))

    assert list(line) == COMMON_KEYS
    assert line["event"] == "edit"
    assert line["session"] == ""
    assert line["agent"] == "unknown"
    assert line["harness_dirty"] is True


def test_query_git_facts_asks_harness_paths_and_branch(
    stop_metrics: ModuleType,
) -> None:
    calls: list[list[str]] = []
    outputs = {"log": "abc1234\n", "status": "", "branch": "feat/x\n"}

    def git(arguments: list[str]) -> str:
        calls.append(arguments)
        return outputs[arguments[0]]

    assert stop_metrics.query_git_facts(git) == GIT
    assert [call[0] for call in calls] == ["log", "status", "branch"]
    for call in calls[:2]:
        assert set(stop_metrics.HARNESS_PATHS) <= set(call)


def test_query_git_facts_treat_failure_as_unknown(stop_metrics: ModuleType) -> None:
    def git(arguments: list[str]) -> str:
        raise OSError(arguments[0])

    assert stop_metrics.query_git_facts(git) == {
        "harness_rev": None,
        "harness_dirty": True,
        "branch": None,
    }


def test_read_transcript_model_reads_only_tail(
    stop_metrics: ModuleType, tmp_path: Path
) -> None:
    transcript = tmp_path / "t.jsonl"
    head = SKILL_CALL + "\n" + "x" * (stop_metrics.TAIL_BYTES + 10) + "\n"
    transcript.write_text(head + SKILL_READ + "\n", encoding="utf-8")

    assert stop_metrics.read_transcript_model(str(transcript)) == "claude-sonnet-5-5"
    assert stop_metrics.read_transcript(str(transcript))[1] == "claude-sonnet-5-5"


def test_read_transcript_model_skips_cut_first_line(
    stop_metrics: ModuleType, tmp_path: Path
) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(SKILL_CALL + "\n" + "x" * 100 + "\n", encoding="utf-8")

    model = stop_metrics.read_transcript_model(str(transcript), size=110)

    assert model is None
    assert stop_metrics.read_transcript_model(str(transcript)) == "claude-opus-5-5"


def test_read_transcript_model_splits_only_on_newline(
    stop_metrics: ModuleType, tmp_path: Path
) -> None:
    transcript = tmp_path / "t.jsonl"
    entry = {
        "type": "assistant",
        "message": {"model": "claude-sonnet-5-5", "content": "a\u2028b\u2029c\x85d"},
    }
    transcript.write_text(
        SKILL_CALL + "\n" + json.dumps(entry, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    assert stop_metrics.read_transcript_model(str(transcript)) == "claude-sonnet-5-5"
    assert stop_metrics.read_transcript(str(transcript))[1] == "claude-sonnet-5-5"


def test_read_transcript_model_keeps_whole_first_line_at_tail_start(
    stop_metrics: ModuleType, tmp_path: Path
) -> None:
    transcript = tmp_path / "t.jsonl"
    tail = SKILL_READ + "\n"
    transcript.write_text("x" * 50 + "\n" + tail, encoding="utf-8")

    model = stop_metrics.read_transcript_model(str(transcript), size=len(tail))

    assert model == "claude-sonnet-5-5"


def test_read_transcript_model_returns_none_without_readable_file(
    stop_metrics: ModuleType, tmp_path: Path
) -> None:
    assert stop_metrics.read_transcript_model(None) is None
    assert stop_metrics.read_transcript_model(str(tmp_path / "missing")) is None
    assert stop_metrics.read_transcript_model(str(tmp_path)) is None


def test_ruff_rules_count_codes_only(stop_metrics: ModuleType) -> None:
    output = (
        "src/a.py:1:89: E501 Line too long (90 > 88)\n"
        "src/a.py:2:89: E501 Line too long (91 > 88)\n"
        "src/a.py:3:1: invalid-syntax: Expected `)`\n"
        "warning: something without a code\n"
    )

    assert stop_metrics.ruff_rules(output) == {"E501": 2, "invalid-syntax": 1}
    assert stop_metrics.ruff_rules("") == {}


def test_record_uses_metrics_log_from_environment(
    stop_metrics: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "m.jsonl"
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(log))

    def git(arguments: list[str]) -> str:
        raise OSError(arguments[0])

    stop_metrics.record(lambda: {"event": "edit"}, git=git)
    stop_metrics.record(lambda: None, git=git)

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "edit"


def test_record_writes_under_common_git_directory(
    stop_metrics: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MOSEMO_METRICS_LOG", raising=False)
    calls: list[list[str]] = []

    def git(arguments: list[str]) -> str:
        calls.append(arguments)
        return f"{tmp_path}\n"

    stop_metrics.record(lambda: {"event": "stop"}, git=git)

    assert calls == [list(stop_metrics.COMMON_DIR)]
    log = tmp_path / stop_metrics.METRICS_FILE
    assert json.loads(log.read_text(encoding="utf-8"))["event"] == "stop"


def test_record_reports_missing_common_directory_once(
    stop_metrics: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("MOSEMO_METRICS_LOG", raising=False)

    def git(arguments: list[str]) -> str:
        raise OSError(arguments[0])

    stop_metrics.record(lambda: {"event": "stop"}, git=git)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "지표 로그 기록 실패: 공통 git 디렉터리를 찾지 못했다"
    ]


def test_record_swallows_build_failure(
    stop_metrics: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    def build() -> dict[str, object]:
        raise LookupError("x")

    stop_metrics.record(build, git=lambda arguments: "")

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("지표 로그 기록 실패")


def test_append_metrics_keeps_lines_whole_across_threads(
    stop_metrics: ModuleType, tmp_path: Path
) -> None:
    log = tmp_path / "m.jsonl"
    rules = {f"R{index:04d}": index for index in range(150)}

    def write(thread: int) -> None:
        for index in range(200):
            fields = {"thread": thread, "index": index, "rules": rules}
            stop_metrics.append_metrics(log, stop_metrics.metrics_line(fields, at=AT))

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(stop_metrics.metrics_line({"rules": rules}, at=AT)) > 2000
    assert len(lines) == 1600
    assert all(isinstance(json.loads(line), dict) for line in lines)


def failing_git(arguments: list[str]) -> str:
    raise OSError(arguments[0])


def stop_outcome(stop_gate: ModuleType) -> object:
    failure = stop_gate.Failure(
        name="ruff check",
        command="ruff check",
        output="src/a.py:1:89: E501 Line too long (90 > 88)\n",
        items=frozenset({"x"}),
    )
    return stop_gate.block_outcome(None, "s", failures=[failure], change=None)


def test_stop_metrics_fields_start_with_common_fields(
    stop_gate: ModuleType,
    stop_metrics: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MOSEMO_AGENT", "claude")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(f"{SKILL_CALL}\n", encoding="utf-8")
    payload = {"session_id": "s1", "transcript_path": str(transcript)}
    outcome = stop_gate.Outcome(0, "allow")

    fields = stop_gate.metrics_fields(payload, outcome=outcome, git=GIT, blocks=1)

    assert list(json.loads(stop_metrics.metrics_line(fields, at=AT))) == [
        *COMMON_KEYS,
        "skills",
        "result",
        "session_blocks",
        "failed",
        "rules",
    ]
    assert fields["event"] == "stop"
    assert fields["session"] == "s1"
    assert fields["agent"] == "claude"
    assert fields["model"] == "claude-opus-5-5"
    assert fields["branch"] == "feat/x"
    assert fields["skills"] == ["engineering-planning"]


def test_record_metrics_failure_does_not_change_stop_outcome(
    stop_gate: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(tmp_path / "missing" / "m.jsonl"))
    before = stop_outcome(stop_gate)
    capsys.readouterr()

    stop_gate.record_metrics(
        {"session_id": "s"}, outcome=before, state_path=None, git=failing_git
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert captured.err.startswith("지표 로그 기록 실패")
    assert stop_outcome(stop_gate) == before


def test_record_metrics_writes_stop_line_with_unknown_git(
    stop_gate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "m.jsonl"
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(log))
    state = tmp_path / "state.json"
    state.write_text('{"s": {"blocks": 2}}', encoding="utf-8")

    stop_gate.record_metrics(
        {"session_id": "s"},
        outcome=stop_gate.Outcome(0, "skip"),
        state_path=state,
        git=failing_git,
    )

    line = json.loads(log.read_text(encoding="utf-8"))
    assert line["event"] == "stop"
    assert line["harness_dirty"] is True
    assert line["harness_rev"] is None
    assert line["branch"] is None
    assert line["session_blocks"] == 2
    assert line["result"] == "skip"
