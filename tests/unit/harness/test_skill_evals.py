"""공통 Skill 평가 실행기 `.codex/evals/run.py`의 명령 만들기·판정·요약(TC-E1~E4).

`claude`·`codex`·subprocess·git을 부르지 않고 대역 줄로 확인한다(#109 S7).
"""

import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

PROMPT = "현재 브랜치 변경을 바탕으로 PR 제목과 본문 초안을 써 줘."
CLAUDE_INIT = json.dumps(
    {"type": "system", "subtype": "init", "model": "claude-opus-5-5"}
)
CLAUDE_SKILL = json.dumps(
    {
        "type": "assistant",
        "message": {
            "model": "claude-opus-5-5",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Skill",
                    "input": {"skill": "pull-request"},
                }
            ],
        },
    }
)
CLAUDE_TEXT = json.dumps(
    {
        "type": "assistant",
        "message": {
            "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "초안입니다."}],
        },
    }
)
CLAUDE_RESULT = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "total_cost_usd": 0.12,
    }
)
CODEX_USAGE = {"input_tokens": 100, "cached_input_tokens": 10, "output_tokens": 5}
CODEX_TURN = json.dumps({"type": "turn.completed", "usage": CODEX_USAGE})


def codex_command_line(command: str) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"id": "item_1", "type": "command_execution", "command": command},
        }
    )


def case(module: ModuleType, case_id: str = "pr-draft") -> Any:
    return module.Case(case_id, "pull-request", True, PROMPT)


def outcome(module: ModuleType, lines: list[str], exit_code: int = 0) -> Any:
    return module.Outcome(exit_code, lines)


def row(module: ModuleType, case_id: str, triggered: bool | None) -> dict:
    return {"case": case_id, "agent": "claude", "triggered": triggered}


# TC-E1 Claude stream-json 판정


def test_claude_skill_call_is_triggered(skill_evals: ModuleType) -> None:
    result = skill_evals.judge(
        case(skill_evals),
        outcome(skill_evals, [CLAUDE_INIT, CLAUDE_SKILL, CLAUDE_RESULT]),
        agent="claude",
        run=1,
    )

    assert result == {
        "case": "pr-draft",
        "agent": "claude",
        "run": 1,
        "triggered": True,
        "expected": True,
        "exit_code": 0,
        "error": None,
        "model": "claude-opus-5-5",
        "cost_usd": 0.12,
        "tokens": None,
    }


def test_claude_without_skill_call_is_not_triggered(skill_evals: ModuleType) -> None:
    result = skill_evals.judge(
        case(skill_evals),
        outcome(skill_evals, [CLAUDE_INIT, CLAUDE_TEXT, CLAUDE_RESULT]),
        agent="claude",
        run=2,
    )

    assert (result["triggered"], result["error"]) == (False, None)


def test_claude_reading_skill_file_is_triggered(skill_evals: ModuleType) -> None:
    read = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {
                            "file_path": "/r/.codex/skills/pull-request/SKILL.md"
                        },
                    }
                ]
            },
        }
    )

    result = skill_evals.judge(
        case(skill_evals, "control-read"),
        outcome(skill_evals, [read, CLAUDE_RESULT]),
        agent="claude",
        run=1,
    )

    assert result["triggered"] is True


def test_claude_error_result_is_null(skill_evals: ModuleType) -> None:
    budget = json.dumps(
        {
            "type": "result",
            "subtype": "error_max_budget_usd",
            "is_error": True,
            "total_cost_usd": 0.5,
        }
    )

    result = skill_evals.judge(
        case(skill_evals),
        outcome(skill_evals, [CLAUDE_SKILL, budget]),
        agent="claude",
        run=1,
    )

    assert (result["triggered"], result["error"], result["cost_usd"]) == (
        None,
        "result error_max_budget_usd",
        0.5,
    )


def test_nonzero_exit_is_null(skill_evals: ModuleType) -> None:
    result = skill_evals.judge(
        case(skill_evals),
        outcome(skill_evals, [CLAUDE_SKILL, CLAUDE_RESULT], exit_code=1),
        agent="claude",
        run=1,
    )

    assert (result["triggered"], result["error"]) == (None, "exit code 1")


def test_failed_start_is_null(skill_evals: ModuleType) -> None:
    failed = skill_evals.Outcome(None, [], "FileNotFoundError: claude")

    result = skill_evals.judge(case(skill_evals), failed, agent="claude", run=1)

    assert (result["triggered"], result["exit_code"], result["error"]) == (
        None,
        None,
        "FileNotFoundError: claude",
    )


def test_claude_unreadable_output_is_null(skill_evals: ModuleType) -> None:
    result = skill_evals.judge(
        case(skill_evals), outcome(skill_evals, ["{broken"]), agent="claude", run=1
    )

    assert result["triggered"] is None
    assert result["error"] == "no readable stream-json line"


# TC-E2 Codex --json 판정


@pytest.mark.parametrize(
    ("command", "triggered"),
    [
        ('/bin/zsh -lc "sed -n 1,80p .codex/skills/pull-request/SKILL.md"', True),
        ("cat /r/.codex/skills/pull-request/SKILL.md", True),
        ("cat .claude/skills/pull-request/SKILL.md", True),
        ("cat .codex/skills/implementation/SKILL.md", False),
        ("cat .codex/skills/pull-request/README.md", False),
    ],
)
def test_codex_command_execution(
    skill_evals: ModuleType, command: str, *, triggered: bool
) -> None:
    lines = [codex_command_line(command), CODEX_TURN]

    result = skill_evals.judge(
        case(skill_evals), outcome(skill_evals, lines), agent="codex", run=1
    )

    assert (result["triggered"], result["error"]) == (triggered, None)
    assert (result["model"], result["tokens"], result["cost_usd"]) == (
        "gpt-6.1-sol",
        CODEX_USAGE,
        None,
    )


def test_codex_ignores_commands_outside_completed_executions(
    skill_evals: ModuleType,
) -> None:
    started = json.dumps(
        {
            "type": "item.started",
            "item": {
                "type": "command_execution",
                "command": "cat .codex/skills/pull-request/SKILL.md",
            },
        }
    )
    message = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "cat .codex/skills/pull-request/SKILL.md",
            },
        }
    )

    result = skill_evals.judge(
        case(skill_evals),
        outcome(skill_evals, [started, message, CODEX_TURN]),
        agent="codex",
        run=1,
    )

    assert result["triggered"] is False


def test_codex_broken_lines_only_is_null(skill_evals: ModuleType) -> None:
    result = skill_evals.judge(
        case(skill_evals),
        outcome(skill_evals, ["not json", '{"type": "item.comp']),
        agent="codex",
        run=1,
    )

    assert (result["triggered"], result["error"]) == (None, "no readable json line")


# TC-E3 명령 만들기와 자식 환경


def test_claude_command(skill_evals: ModuleType) -> None:
    command = skill_evals.claude_command()

    assert command == [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "claude-opus-5-5",
        "--max-budget-usd",
        "0.5",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        "Read,Grep,Glob,Skill",
        "--strict-mcp-config",
        "--settings",
        '{"disableAllHooks": true}',
        "--no-session-persistence",
    ]


def test_codex_command(skill_evals: ModuleType) -> None:
    command = skill_evals.codex_command()

    assert command == [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "--disable",
        "hooks",
        "-m",
        "gpt-6.1-sol",
        "-c",
        'model_reasoning_effort="low"',
    ]


def test_commands_do_not_carry_prompts(
    skill_evals: ModuleType, repository: Path
) -> None:
    cases = skill_evals.read_cases(
        (repository / ".codex/evals/cases.csv").read_text(encoding="utf-8")
    )
    arguments = skill_evals.claude_command() + skill_evals.codex_command()

    assert not [
        each for each in cases for argument in arguments if each.prompt in argument
    ]


def test_execute_passes_prompt_on_stdin(
    skill_evals: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_run(command: list[str], **options: Any) -> Any:
        calls.append((command, options))
        return SimpleNamespace(
            returncode=0, stdout=f"{CLAUDE_SKILL}\n{CLAUDE_RESULT}\n"
        )

    monkeypatch.setattr(skill_evals.subprocess, "run", fake_run)
    monkeypatch.setattr(skill_evals.os, "environ", {"CLAUDECODE": "1", "HOME": "/h"})
    command = skill_evals.claude_command()

    result = skill_evals.execute(command, PROMPT)

    assert result == skill_evals.Outcome(0, [CLAUDE_SKILL, CLAUDE_RESULT])
    assert calls == [
        (
            command,
            {
                "input": PROMPT,
                "capture_output": True,
                "text": True,
                "cwd": skill_evals.ROOT,
                "env": {"HOME": "/h"},
                "timeout": skill_evals.TIMEOUT_SECONDS,
                "check": False,
            },
        )
    ]


def test_execute_failure_is_reported(
    skill_evals: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **_: Any) -> Any:
        raise skill_evals.subprocess.TimeoutExpired(command, 600)

    monkeypatch.setattr(skill_evals.subprocess, "run", fake_run)

    result = skill_evals.execute(["codex"], PROMPT)

    assert (result.exit_code, result.lines) == (None, [])
    assert result.error.startswith("TimeoutExpired: ")


def test_child_env_removes_only_parent_session_markers(
    skill_evals: ModuleType,
) -> None:
    parent = {
        "CLAUDECODE": "1",
        "CLAUDE_PID": "123",
        "CLAUDE_CODE_SESSION_ID": "s",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "ANTHROPIC_API_KEY": "key",
        "OPENAI_API_KEY": "key",
        "HOME": "/home/me",
        "PATH": "/bin",
        "CLAUDE_CONFIG_DIR": "/c",
    }

    assert skill_evals.child_env(parent) == {
        "ANTHROPIC_API_KEY": "key",
        "OPENAI_API_KEY": "key",
        "HOME": "/home/me",
        "PATH": "/bin",
        "CLAUDE_CONFIG_DIR": "/c",
    }


# TC-E4 문제 파일


def test_cases_file(skill_evals: ModuleType, repository: Path) -> None:
    text = (repository / ".codex/evals/cases.csv").read_text(encoding="utf-8")
    cases = skill_evals.read_cases(text)
    skills = {path.name for path in (repository / ".codex/skills").iterdir()}
    claude_runs = sum(each.runs for each in cases)

    assert text.splitlines()[0] == "id,skill,should_trigger,prompt"
    assert {each.skill for each in cases} <= skills
    assert "control-read" in {each.id for each in cases}
    assert claude_runs * float(skill_evals.CLAUDE_BUDGET_USD) <= 2


def test_control_cases_run_once(skill_evals: ModuleType) -> None:
    assert [case(skill_evals, "control-read").runs, case(skill_evals).runs] == [1, 3]


def test_read_cases_parses_should_trigger(skill_evals: ModuleType) -> None:
    text = "id,skill,should_trigger,prompt\na,pull-request,true,p\nb,x,false,q\n"

    assert skill_evals.read_cases(text) == [
        skill_evals.Case("a", "pull-request", True, "p"),
        skill_evals.Case("b", "x", False, "q"),
    ]


# 종료 코드와 요약표


@pytest.mark.parametrize(
    ("rows", "status"),
    [
        ([("pr-draft", False), ("control-read", True)], 0),
        ([("pr-draft", None), ("control-read", True)], 1),
        ([("pr-draft", True), ("control-read", False)], 1),
        ([("pr-draft", True), ("control-read", None)], 1),
    ],
)
def test_exit_status(
    skill_evals: ModuleType, rows: list[tuple[str, bool | None]], status: int
) -> None:
    results = [row(skill_evals, case_id, triggered) for case_id, triggered in rows]

    assert skill_evals.exit_status(results) == status


def test_summary_counts_by_case_and_agent(skill_evals: ModuleType) -> None:
    rows = [
        {"case": "pr-draft", "agent": "claude", "expected": True, "triggered": True},
        {"case": "pr-draft", "agent": "claude", "expected": True, "triggered": False},
        {"case": "pr-draft", "agent": "claude", "expected": True, "triggered": None},
        {"case": "pr-draft", "agent": "codex", "expected": True, "triggered": True},
    ]

    assert skill_evals.summary(rows).splitlines() == [
        "| case | agent | expected | true | false | null |",
        "| --- | --- | --- | --- | --- | --- |",
        "| pr-draft | claude | true | 1 | 1 | 1 |",
        "| pr-draft | codex | true | 1 | 0 | 0 |",
    ]
