"""Codex 세션 기록 파서와 Codex Stop·편집·리뷰 줄(TC-4)."""

import json
import time
from pathlib import Path
from types import ModuleType

import pytest

SESSION = "01a1baeb-089d-7892-a3a3-2d962fb98758"
SESSION_META = json.dumps(
    {
        "type": "session_meta",
        "payload": {"id": SESSION, "cli_version": "0.167.0"},
    }
)
TURN_CONTEXT = json.dumps(
    {
        "type": "turn_context",
        "payload": {"turn_id": "t1", "model": "gpt-6-luna"},
    }
)
SKILL_MESSAGE = json.dumps(
    {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "구현해"},
                {
                    "type": "input_text",
                    "text": (
                        "<skill>\n<name>implementation</name>\n"
                        "<path>/repo/.codex/skills/implementation/SKILL.md</path>\n"
                        "---\nname: other\n</skill>"
                    ),
                },
            ],
        },
    }
)
EXEC_CALL = json.dumps(
    {
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "input": (
                'text(await tools.exec_command({cmd:"sed -n 1,80p '
                '.codex/skills/verification/SKILL.md"}));'
            ),
        },
    }
)
COMMAND_EXECUTION = json.dumps(
    {
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "CommandExecution",
                "command": [
                    "/bin/zsh",
                    "-lc",
                    "sed -n 1,80p .codex/skills/verification/SKILL.md",
                ],
            },
        },
    }
)
LINES = [
    SESSION_META,
    TURN_CONTEXT,
    SKILL_MESSAGE,
    '{"type":"response_item",',
    EXEC_CALL,
    COMMAND_EXECUTION,
]
GIT = {"harness_rev": "08edbe3", "harness_dirty": False, "branch": "feat/x"}
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
AT = time.gmtime(0)


def entry(kind: str, payload: dict[str, object]) -> str:
    return json.dumps({"type": kind, "payload": payload})


def write_transcript(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def codex_session_file(home: Path, session: str = SESSION) -> Path:
    path = home / "sessions/2026/11/08" / f"rollout-2026-11-08T20-28-47-{session}.jsonl"
    return write_transcript(path, LINES)


def test_codex_transcript_facts_find_skills_and_turn_context_model(
    stop_metrics: ModuleType,
) -> None:
    skills, model = stop_metrics.codex_transcript_facts(LINES)

    assert skills == ["implementation", "verification"]
    assert model == "gpt-6-luna"


def test_codex_transcript_facts_read_function_call_arguments(
    stop_metrics: ModuleType,
) -> None:
    call = entry(
        "response_item",
        {
            "type": "function_call",
            "arguments": json.dumps({"cmd": "cat /w/.claude/skills/spec/SKILL.md"}),
        },
    )

    assert stop_metrics.codex_transcript_facts([call]) == (["spec"], None)


def test_codex_transcript_facts_ignore_other_sources(
    stop_metrics: ModuleType,
) -> None:
    assistant = entry(
        "response_item",
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "<skill><name>x</name>"}],
        },
    )
    other_file = EXEC_CALL.replace("verification/SKILL.md", "verification/README.md")
    prefixed = EXEC_CALL.replace(".codex/skills", "x.codex/skills")
    output = entry(
        "response_item",
        {"type": "custom_tool_call_output", "output": ".codex/skills/y/SKILL.md"},
    )

    skills, model = stop_metrics.codex_transcript_facts(
        [
            assistant,
            other_file,
            prefixed,
            output,
        ]
    )

    assert skills == []
    assert model is None


def test_codex_transcript_facts_keep_last_turn_context_model(
    stop_metrics: ModuleType,
) -> None:
    later = TURN_CONTEXT.replace("gpt-6-luna", "gpt-6.1-sol")

    assert stop_metrics.codex_transcript_facts([TURN_CONTEXT, later])[1] == (
        "gpt-6.1-sol"
    )


def test_session_facts_prefer_hook_input_model_for_codex(
    stop_metrics: ModuleType,
) -> None:
    with_model = stop_metrics.session_facts(
        {"model": "gpt-6.1-sol"}, LINES, agent="codex"
    )
    without_model = stop_metrics.session_facts({"model": 3}, LINES, agent="codex")

    assert with_model == (["implementation", "verification"], "gpt-6.1-sol")
    assert without_model == (["implementation", "verification"], "gpt-6-luna")


def test_session_facts_use_claude_parser_for_other_agents(
    stop_metrics: ModuleType,
) -> None:
    claude = '{"type":"assistant","message":{"model":"claude-opus-5-5"}}'

    assert stop_metrics.session_facts(
        {"model": "gpt-6.1-sol"}, [*LINES, claude], agent="claude"
    ) == ([], "claude-opus-5-5")


def test_read_session_finds_codex_transcript_by_session_id(
    stop_metrics: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    codex_session_file(tmp_path)
    codex_session_file(tmp_path, "other-session")

    found = stop_metrics.read_session({"session_id": SESSION}, agent="codex")

    assert found == (["implementation", "verification"], "gpt-6-luna")


def test_read_session_uses_transcript_path_first(
    stop_metrics: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    codex_session_file(tmp_path)
    given = write_transcript(tmp_path / "given.jsonl", [EXEC_CALL])

    found = stop_metrics.read_session(
        {"session_id": SESSION, "transcript_path": str(given)}, agent="codex"
    )

    assert found == (["verification"], None)


def test_read_session_without_codex_transcript_keeps_input_model(
    stop_metrics: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    codex_session_file(tmp_path)

    missing = stop_metrics.read_session(
        {"session_id": "missing", "model": "gpt-6-luna"}, agent="codex"
    )
    no_session = stop_metrics.read_session({}, agent="codex")

    assert missing == ([], "gpt-6-luna")
    assert no_session == ([], None)


def test_find_codex_transcript_takes_first_sorted_match(
    stop_metrics: ModuleType, tmp_path: Path
) -> None:
    later = codex_session_file(tmp_path)
    earlier = write_transcript(
        tmp_path
        / "sessions/2026/11/07"
        / f"rollout-2026-11-07T01-00-00-{SESSION}.jsonl",
        [],
    )

    assert stop_metrics.find_codex_transcript(tmp_path, SESSION) == str(earlier)
    assert later.exists()
    assert stop_metrics.find_codex_transcript(tmp_path, "../x") is None


def test_claude_does_not_search_codex_sessions(
    stop_metrics: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    codex_session_file(tmp_path)

    assert stop_metrics.transcript_file({"session_id": SESSION}, agent="claude") is None


def test_codex_stop_line_has_common_fields_and_codex_facts(
    stop_gate: ModuleType,
    stop_metrics: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOSEMO_AGENT", "codex")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    codex_session_file(tmp_path)

    fields = stop_gate.metrics_fields(
        {"session_id": SESSION},
        outcome=stop_gate.Outcome(0, "allow"),
        git=GIT,
        blocks=0,
    )

    line = json.loads(stop_metrics.metrics_line(fields, at=AT))
    assert list(line)[: len(COMMON_KEYS)] == COMMON_KEYS
    assert line["agent"] == "codex"
    assert line["model"] == "gpt-6-luna"
    assert line["skills"] == ["implementation", "verification"]
    assert isinstance(line["harness_dirty"], bool)


def test_codex_edit_line_uses_input_model_then_transcript(
    post_edit_feedback: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = tmp_path / "m.jsonl"
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(log))
    monkeypatch.setenv("MOSEMO_AGENT", "codex")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    codex_session_file(tmp_path)

    def git(arguments: list[str]) -> str:
        return {"log": "08edbe3\n", "status": "", "branch": "feat/x\n"}[arguments[0]]

    for payload in ({"session_id": SESSION}, {"session_id": SESSION, "model": "m2"}):
        edit = post_edit_feedback.Edit(payload, tmp_path, ["a.py"], output="")
        post_edit_feedback.record_edit(edit, git=git, deadline=time.monotonic() + 60)

    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [line["model"] for line in lines] == ["gpt-6-luna", "m2"]
    assert list(lines[0])[: len(COMMON_KEYS)] == COMMON_KEYS
    assert lines[0]["event"] == "edit"
    assert lines[0]["agent"] == "codex"
    assert isinstance(lines[0]["harness_dirty"], bool)


def test_codex_review_line_has_common_fields_and_codex_model(
    review_metrics: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = tmp_path / "m.jsonl"
    monkeypatch.setenv("MOSEMO_METRICS_LOG", str(log))
    monkeypatch.setenv("MOSEMO_AGENT", "codex")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    codex_session_file(tmp_path)
    payload = {
        "session_id": SESSION,
        "agent_type": "code-reviewer",
        "last_assistant_message": "REVIEW high=0 medium=1 low=2\n...",
    }

    def git(arguments: list[str]) -> str:
        return {"log": "08edbe3\n", "status": " M x\n", "branch": "feat/x\n"}[
            arguments[0]
        ]

    review_metrics.record_review(payload, code=None, git=git)

    line = json.loads(log.read_text(encoding="utf-8"))
    assert list(line)[: len(COMMON_KEYS)] == COMMON_KEYS
    assert line["event"] == "review"
    assert line["agent"] == "codex"
    assert line["model"] == "gpt-6-luna"
    assert line["harness_dirty"] is True
    assert line["findings"] == {"high": 0, "medium": 1, "low": 2}
