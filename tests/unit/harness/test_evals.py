import ast
import json
import os
from pathlib import Path
from types import ModuleType

import pytest

BASE = "f4a4fc2c9121580c850270962bd194b32bccda06"


@pytest.mark.parametrize("name", ["E1", "E2", "E3"])
def test_task_files_fix_base_budget_and_prompt(
    eval_runner: ModuleType, repository: Path, name: str
) -> None:
    text = (repository / f".codex/evals/tasks/{name}.md").read_text(encoding="utf-8")

    task = eval_runner.parse_task(name, text)

    assert task.base == BASE
    assert task.budget_usd == 3
    assert task.prompt.strip()
    assert "## 지시문" not in task.prompt


def test_e3_prompt_requires_implementation_skill(
    eval_runner: ModuleType, repository: Path
) -> None:
    text = (repository / ".codex/evals/tasks/E3.md").read_text(encoding="utf-8")

    assert "implementation Skill" in eval_runner.parse_task("E3", text).prompt


def test_parse_task_rejects_missing_fields(eval_runner: ModuleType) -> None:
    with pytest.raises(ValueError, match="E9"):
        eval_runner.parse_task("E9", "- base: abc\n\n## 지시문\n하라\n")


def test_harness_paths_match_stop_gate(
    eval_runner: ModuleType, repository: Path
) -> None:
    source = (repository / ".codex/hooks/stop_gate.py").read_text(encoding="utf-8")
    assignment = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "HARNESS_PATHS"
            for target in node.targets
        )
    )

    assert ast.literal_eval(assignment.value) == eval_runner.HARNESS_PATHS


VALIDATE_SKIPPED_PREFIXES = ("#", "set ", "script_dir=", "cd ")


def bash_rule_allows(rule: str, command: str) -> bool:
    """Claude Code `Bash(...)` 규칙 중 끝 ` *` 하나 또는 와일드카드 없는 형태만 맞음으로 본다."""
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return False
    body = rule[len("Bash(") : -1]
    if "*" not in body:
        return command == body
    if body.endswith(" *") and body.count("*") == 1:
        prefix = body[: -len(" *")]
        return command == prefix or command.startswith(prefix + " ")
    return False


def validate_commands(script: str) -> list[str]:
    return [
        line
        for line in (raw.strip() for raw in script.splitlines())
        if line and not line.startswith(VALIDATE_SKIPPED_PREFIXES)
    ]


def test_bash_rule_allows_trailing_wildcard_and_exact_rules() -> None:
    assert bash_rule_allows("Bash(uv run *)", "uv run --locked ruff check .")
    assert not bash_rule_allows("Bash(uv run *)", "uv build")
    assert bash_rule_allows("Bash(uv build *)", "uv build")
    assert bash_rule_allows("Bash(git diff --check)", "git diff --check")
    assert not bash_rule_allows("Bash(uv * build)", "uv x build")
    assert not bash_rule_allows("Read", "uv build")


def test_validate_commands_are_allowed_tools(
    eval_runner: ModuleType, repository: Path
) -> None:
    script = (repository / ".codex/skills/verification/scripts/validate.sh").read_text(
        encoding="utf-8"
    )
    commands = validate_commands(script)

    denied = [
        command
        for command in commands
        if not any(
            bash_rule_allows(rule, command) for rule in eval_runner.ALLOWED_TOOLS
        )
    ]

    assert "uv build" in commands
    assert denied == []


def repository_paths(source: str) -> set[str]:
    """`repository`·`REPOSITORY`에서 시작하는 가장 바깥 `/` 식의 상수 경로 접두어."""
    divisions = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
    ]
    inner = {id(node.left) for node in divisions}
    paths = set()
    for node in divisions:
        if id(node) in inner:
            continue
        path = division_path(node)
        if path is not None and path not in {"", "/"}:
            paths.add(path)
    return paths


def division_path(node: ast.BinOp) -> str | None:
    operands: list[ast.expr] = []
    left: ast.expr = node
    while isinstance(left, ast.BinOp) and isinstance(left.op, ast.Div):
        operands.append(left.right)
        left = left.left
    if not (isinstance(left, ast.Name) and left.id in {"repository", "REPOSITORY"}):
        return None
    path = ""
    for operand in reversed(operands):
        text, complete = constant_prefix(operand)
        # 이어 붙일 때 앞 조각 끝의 `/`는 구분자 하나로 본다(`"docs/" / name` → `docs/`).
        if text:
            path = f"{path.rstrip('/')}/{text}" if path else text
        if not complete:
            return path if path.endswith("/") else path + "/"
    return path


def constant_prefix(node: ast.expr) -> tuple[str, bool]:
    """조각의 앞쪽 상수 문자열과 조각 전체가 상수인지."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, True
    if isinstance(node, ast.JoinedStr):
        prefix = ""
        for value in node.values:
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                return prefix, False
            prefix += value.value
        return prefix, True
    return "", False


def test_repository_paths_collects_outermost_constant_prefixes() -> None:
    source = (
        'a = repository / "pyproject.toml"\n'
        'b = REPOSITORY / f"docs/{name}.md"\n'
        'c = repository / ".codex" / "x"\n'
        'd = repository / "a" / name\n'
    )

    assert repository_paths(source) == {"pyproject.toml", "docs/", ".codex/x", "a/"}


def test_repository_paths_treats_trailing_slash_as_one_separator() -> None:
    source = (
        'a = repository / "docs/" / name\n'
        'b = repository / "docs/" / "x"\n'
        'c = repository / "docs/adr/"\n'
        'd = repository / "e/" / f"f/{x}"\n'
    )

    assert repository_paths(source) == {"docs/", "docs/x", "docs/adr/", "e/f/"}


def test_files_read_by_harness_tests_are_harness_paths(
    eval_runner: ModuleType,
) -> None:
    paths = {
        path
        for module in sorted(Path(__file__).parent.glob("*.py"))
        for path in repository_paths(module.read_text(encoding="utf-8"))
    }

    assert ".pre-commit-config.yaml" in paths
    assert sorted(p for p in paths if not eval_runner.is_harness_path(p)) == []


def test_child_environment_drops_parent_session_and_keeps_auth(
    eval_runner: ModuleType,
) -> None:
    parent = {
        "PATH": "/bin",
        "HOME": "/home/me",
        "ANTHROPIC_API_KEY": "key",
        "CLAUDE_CONFIG_DIR": "/home/me/.claude",
        "CLAUDE_CODE_OAUTH_TOKEN": "token",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_CODE_SESSION_ID": "parent",
        "CLAUDE_EFFORT": "high",
        "CLAUDE_PID": "1",
        "AI_AGENT": "claude",
        "HERDR_PANE_ID": "p1",
        "MOSEMO_HARNESS_REV": "old",
        "GIT_DIR": "/repo/.git",
        "VIRTUAL_ENV": "/repo/.venv",
        "GH_TOKEN": "gh",
        "GITHUB_TOKEN": "gh",
        "GH_ENTERPRISE_TOKEN": "gh",
        "GITHUB_ENTERPRISE_TOKEN": "gh",
        "GH_CONFIG_DIR": "/home/me/.config/gh",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
    }

    env = eval_runner.child_environment(
        parent, {"MOSEMO_AGENT": "claude", "GH_CONFIG_DIR": "/tmp/empty"}
    )

    assert env == {
        "PATH": "/bin",
        "HOME": "/home/me",
        "ANTHROPIC_API_KEY": "key",
        "CLAUDE_CONFIG_DIR": "/home/me/.claude",
        "CLAUDE_CODE_OAUTH_TOKEN": "token",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "MOSEMO_AGENT": "claude",
        "GH_CONFIG_DIR": "/tmp/empty",
    }


def test_claude_command_fixes_model_budget_and_isolation(
    eval_runner: ModuleType,
) -> None:
    task = eval_runner.Task(name="E3", base=BASE, budget_usd=3.0, prompt="하라\n")

    command = eval_runner.claude_command(task, session="sid")

    def value(flag: str) -> str:
        return command[command.index(flag) + 1]

    assert command[:2] == ["claude", "-p"]
    assert value("--model") == "claude-opus-5-5"
    assert value("--max-budget-usd") == "3"
    assert value("--session-id") == "sid"
    assert value("--permission-mode") == "acceptEdits"
    assert value("--setting-sources") == "project"
    assert value("--output-format") == "json"
    assert "--strict-mcp-config" in command
    assert "--bare" not in command
    assert {"Edit", "Write", "Read", "Grep", "Glob"} <= set(
        value("--allowedTools").split(",")
    )
    assert value("--disallowedTools").split(",") == [
        "Bash(git push *)",
        "Bash(gh *)",
    ]


@pytest.mark.parametrize(
    ("result", "status"),
    [
        ({"subtype": "success", "is_error": False}, "completed"),
        ({"subtype": "success", "is_error": True}, "error"),
        ({"subtype": "error_max_budget_usd", "is_error": True}, "budget_exceeded"),
        ({"subtype": "error_during_execution", "is_error": True}, "error"),
    ],
)
def test_claude_outcome_maps_subtype_to_status(
    eval_runner: ModuleType, result: dict[str, object], status: str
) -> None:
    stdout = json.dumps(
        {
            "type": "result",
            **result,
            "total_cost_usd": 0.42,
            "num_turns": 7,
            "permission_denials": [{"tool_name": "Bash"}],
        }
    )

    outcome = eval_runner.claude_outcome(stdout, 0)

    assert outcome.status == status
    assert (outcome.cost_usd, outcome.turns, outcome.permission_denials) == (
        0.42,
        7,
        1,
    )


def test_claude_outcome_treats_unparsable_output_as_error(
    eval_runner: ModuleType,
) -> None:
    outcome = eval_runner.claude_outcome("Error: not logged in\n", 1)

    assert outcome.status == "error"
    assert outcome.cost_usd is None
    assert "not logged in" in outcome.detail


def test_added_tests_finds_new_functions_and_methods(eval_runner: ModuleType) -> None:
    before = {
        "tests/unit/observation/test_a.py": "def test_old():\n    pass\n",
    }
    after = {
        "tests/unit/observation/test_a.py": (
            "def test_old():\n    pass\n\n"
            "async def test_new():\n    pass\n\n"
            "def helper():\n    pass\n"
        ),
        "tests/unit/labels/test_b.py": (
            "class TestB:\n    def test_case(self):\n        pass\n"
            "    def helper(self):\n        pass\n"
        ),
        "tests/unit/observation/test_broken.py": "def test_x(:\n",
        "tests/unit/labels/focus_test.py": "def test_suffix():\n    pass\n",
    }

    assert eval_runner.added_tests(before, after) == [
        "tests/unit/labels/focus_test.py::test_suffix",
        "tests/unit/labels/test_b.py::TestB::test_case",
        "tests/unit/observation/test_a.py::test_new",
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("tests/unit/observation/test_models.py", True),
        ("tests/unit/observation/models_test.py", True),
        ("tests/unit/observation/conftest.py", False),
        ("tests/unit/observation/test_data.json", False),
    ],
)
def test_is_test_file_follows_pytest_defaults(
    eval_runner: ModuleType, name: str, expected: bool
) -> None:
    assert eval_runner.is_test_file(name) is expected


def test_code_tree_hash_ignores_harness_files(eval_runner: ModuleType) -> None:
    code = "100644 blob aaa\tsrc/mosemo/labels/router.py\n"
    harness = (
        "100644 blob bbb\tsrc/mosemo/AGENTS.md\n100644 blob ccc\tsrc/mosemo/CLAUDE.md\n"
    )
    changed = "100644 blob ddd\tsrc/mosemo/labels/router.py\n"

    assert eval_runner.code_tree_hash(code + harness) == eval_runner.code_tree_hash(
        code
    )
    assert eval_runner.code_tree_hash(changed) != eval_runner.code_tree_hash(code)


def test_state_digest_changes_with_watched_files_and_skips_fifo(
    eval_runner: ModuleType, tmp_path: Path
) -> None:
    git_dir = tmp_path / "repo.git"
    (git_dir / "hooks").mkdir(parents=True)
    (git_dir / "worktrees" / "other").mkdir(parents=True)
    (git_dir / "config").write_text("[core]\n", encoding="utf-8")
    skill = tmp_path / "worktree" / ".codex" / "skills" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# skill\n", encoding="utf-8")
    home = tmp_path / "home"

    def digest() -> str | None:
        files = eval_runner.watched_files([git_dir], home=home, harness_files=[skill])
        return eval_runner.state_digest(files)

    digests = [digest()]
    changes = [
        git_dir / "hooks" / "pre-commit",
        git_dir / "worktrees" / "other" / "config.worktree",
        skill,
    ]
    for path in changes:
        path.write_text("changed\n", encoding="utf-8")
        digests.append(digest())
    home.mkdir()
    os.mkfifo(home / ".gitconfig")
    digests.append(digest())
    (home / ".config" / "git").mkdir(parents=True)
    (home / ".config" / "git" / "config").symlink_to(tmp_path / "a")
    digests.append(digest())
    (home / ".config" / "git" / "config").unlink()
    (home / ".config" / "git" / "config").symlink_to(tmp_path / "b")
    digests.append(digest())

    assert None not in digests
    assert len(set(digests)) == len(digests)


def test_state_digest_is_unknown_when_a_file_cannot_be_read(
    eval_runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def unreadable(path: Path) -> bytes:
        raise PermissionError(path)

    monkeypatch.setattr(eval_runner, "file_signature", unreadable)

    assert eval_runner.state_digest([tmp_path / "config"]) is None


def test_remote_reachable_treats_timeout_as_reachable(
    eval_runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def slow(*_: object, **__: object) -> None:
        raise eval_runner.subprocess.TimeoutExpired("git", 60)

    monkeypatch.setattr(eval_runner, "execute", slow)

    assert eval_runner.remote_reachable(tmp_path, url="git@host:r.git", env={})


def test_label_acceptance_writes_the_preloaded_judge(
    eval_runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / eval_runner.ACCEPTANCE_TEST
    target.parent.mkdir(parents=True)
    target.write_bytes(b"tampered\n")
    commands = []

    def passing(command: list[str], **_: object) -> object:
        commands.append(command)
        return eval_runner.subprocess.CompletedProcess(command, 0, "1 passed\n", "")

    monkeypatch.setattr(eval_runner, "execute", passing)

    passed, detail = eval_runner.label_acceptance(
        tmp_path, env={}, acceptance=b"judge\n"
    )

    assert target.read_bytes() == b"judge\n"
    assert (passed, detail) == (True, {"pytest": "1 passed"})
    assert commands[0][-1] == eval_runner.ACCEPTANCE_TEST


def test_judge_safely_records_judgement_error(
    eval_runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def broken_judge(*_: object, **__: object) -> None:
        raise RuntimeError("git show failed")

    monkeypatch.setattr(eval_runner, "judge", broken_judge)
    task = eval_runner.Task(name="E3", base=BASE, budget_usd=3.0, prompt="하라\n")

    judgement, error = eval_runner.judge_safely(task, tmp_path, env={}, acceptance=b"")
    line = eval_runner.result_line(
        eval_runner.Task(name="E3", base=BASE, budget_usd=3.0, prompt="하라\n"),
        eval_runner.ClaudeOutcome("completed", 1.0, 1, 0),
        judgement=judgement,
        context={"judgement_error": error},
    )

    assert (judgement, error) == (None, "RuntimeError: git show failed")
    assert (line["acceptance"], line["validate"], line["judgement"]) == (None, None, {})


def test_isolated_environment_blocks_user_credentials(
    eval_runner: ModuleType, tmp_path: Path
) -> None:
    context = {"metrics_log": "/repo/.git/m.jsonl", "harness_rev": "abc"}

    env = eval_runner.isolated_environment(
        tmp_path, context={**context, "harness_dirty": False}
    )

    assert env["GH_CONFIG_DIR"] == str(tmp_path / "gh")
    assert not any((tmp_path / "gh").iterdir())
    assert env["GIT_CONFIG_GLOBAL"] == str(tmp_path / "gitconfig")
    assert (tmp_path / "gitconfig").read_text(encoding="utf-8") == ""
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_SSH_COMMAND"] == "false"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["MOSEMO_HARNESS_DIRTY"] == "false"
    assert "SSH_AUTH_SOCK" not in env
    assert not {"GH_TOKEN", "GITHUB_TOKEN"} & set(env)


@pytest.mark.parametrize(
    ("output", "returncode", "count"),
    [
        ("\nno tests collected (40 deselected) in 0.1s\n", 5, 0),
        ("tests/unit/x.py::test_a\n\n3/120 tests collected (117 deselected)\n", 0, 3),
        ("tests/unit/x.py::test_a\n\n1 test collected in 0.2s\n", 0, 1),
        ("ERROR collecting tests/unit/x.py\n", 2, None),
    ],
)
def test_collected_count_reads_pytest_summary(
    eval_runner: ModuleType, output: str, returncode: int, count: int | None
) -> None:
    assert eval_runner.collected_count(output, returncode) == count


def test_session_metrics_keeps_lines_of_the_session(eval_runner: ModuleType) -> None:
    lines = [
        '{"session": "a", "result": "block"}',
        "not json",
        '{"session": "b", "result": "allow"}',
        '{"session": "a", "result": "allow"}',
    ]

    assert eval_runner.session_metrics(lines, "a") == [
        {"session": "a", "result": "block"},
        {"session": "a", "result": "allow"},
    ]


def test_result_line_records_outcome_judgement_and_context(
    eval_runner: ModuleType,
) -> None:
    task = eval_runner.Task(name="E1", base=BASE, budget_usd=3.0, prompt="하라\n")
    outcome = eval_runner.ClaudeOutcome("completed", 1.25, 30, 0)
    judgement = eval_runner.Judgement(
        acceptance=True, validate=False, detail={"pytest": "ok"}
    )

    line = eval_runner.result_line(
        task, outcome, judgement=judgement, context={"run": 2, "harness_rev": "abc"}
    )

    assert line == {
        "run": 2,
        "harness_rev": "abc",
        "task": "E1",
        "base": BASE,
        "model": "claude-opus-5-5",
        "budget_usd": 3.0,
        "status": "completed",
        "status_detail": "",
        "cost_usd": 1.25,
        "turns": 30,
        "permission_denials": 0,
        "acceptance": True,
        "validate": False,
        "judgement": {"pytest": "ok"},
    }


def test_instruction_conflicts_accepts_workspace_outside_home(
    eval_runner: ModuleType, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert eval_runner.instruction_conflicts(workspace, home=tmp_path / "home") == []


def test_instruction_conflicts_reports_home_and_ancestor_claude_md(
    eval_runner: ModuleType, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    workspace = home / "projects" / "workspace"
    workspace.mkdir(parents=True)
    (home / "CLAUDE.md").write_text("user\n", encoding="utf-8")
    (home / ".claude").mkdir()
    (home / ".claude" / "CLAUDE.md").write_text("user\n", encoding="utf-8")
    (home / "projects" / "CLAUDE.local.md").write_text("local\n", encoding="utf-8")

    (workspace / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")

    problems = eval_runner.instruction_conflicts(workspace, home=home)

    found = "상위 폴더에 프로젝트 지시 파일이 있다"
    assert problems == [
        f"작업 공간이 홈 아래에 있다: {workspace.resolve()}",
        f"{found}: {home.resolve() / 'projects' / 'CLAUDE.local.md'}",
        f"{found}: {home.resolve() / 'CLAUDE.md'}",
        f"{found}: {home.resolve() / '.claude' / 'CLAUDE.md'}",
    ]


def test_transcript_counts_dedupes_message_ids_and_counts_tool_uses(
    eval_runner: ModuleType,
) -> None:
    lines = [
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"text"}]}}',
        (
            '{"type":"assistant","message":{"id":"m1","content":'
            '[{"type":"tool_use","name":"Read"}]}}'
        ),
        '{"type":"user","message":{"content":[{"type":"tool_result"}]}}',
        (
            '{"type":"assistant","message":{"id":"m2","content":'
            '[{"type":"tool_use","name":"Bash"},{"type":"tool_use","name":"Edit"}]}}'
        ),
        "not json",
        "[]",
    ]

    assert eval_runner.transcript_counts(lines) == {
        "assistant_messages": 2,
        "tool_uses": 3,
    }


def test_transcript_counts_are_unknown_without_transcript(
    eval_runner: ModuleType,
) -> None:
    assert eval_runner.transcript_counts([]) == {
        "assistant_messages": None,
        "tool_uses": None,
    }


def result_row(
    *, rev: str, acceptance: bool, cost: float, metrics: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "harness_rev": rev,
        "harness_dirty": False,
        "model": "claude-opus-5-5",
        "claude_version": "2.1.319 (Claude Code)",
        "task": "E3",
        "status": "completed",
        "acceptance": acceptance,
        "validate": True,
        "cost_usd": cost,
        "turns": 1,
        "judgement_error": "",
        "assistant_messages": 20,
        "tool_uses": 12,
        "metrics": metrics,
    }


def test_spread_is_min_median_max_without_missing(eval_report: ModuleType) -> None:
    assert eval_report.spread([3.0, None, 1.0, 2.0]) == "1 / 2 / 3"
    assert eval_report.spread([None]) == "-"


def test_report_groups_by_revision_and_summarizes_metrics(
    eval_report: ModuleType,
) -> None:
    first: dict[str, object] = {
        "rules": {"F401": 2},
        "session_blocks": 1,
        "skills": ["implementation"],
    }
    last: dict[str, object] = {
        "rules": {},
        "session_blocks": 1,
        "skills": ["implementation"],
    }
    rows = [
        result_row(rev="aaa", acceptance=True, cost=1.0, metrics=[first, last]),
        result_row(rev="aaa", acceptance=False, cost=2.0, metrics=[last]),
        result_row(rev="bbb", acceptance=True, cost=0.5, metrics=[]),
    ]

    lines = eval_report.report(rows).splitlines()

    assert len(lines) == 4
    assert lines[1].count("---") == lines[0].count("|") - 1
    assert lines[2] == (
        "| aaa | False | claude-opus-5-5 | 2.1.319 (Claude Code) | E3 | 2 | completed 2 "
        "| 0/2 | 0 / 0.5 / 1 | 1 / 1 / 1 | F401 0 / 1 / 2 | 1 / 1 / 1 | 1 / 1.5 / 2 "
        "| 1 / 1 / 1 | 20 / 20 / 20 | 12 / 12 / 12 | implementation 2/2 |"
    )
    assert lines[3] == (
        "| bbb | False | claude-opus-5-5 | 2.1.319 (Claude Code) | E3 | 1 | completed 1 "
        "| 0/1 | 1 / 1 / 1 | 1 / 1 / 1 | - | - | 0.5 / 0.5 / 0.5 | 1 / 1 / 1 "
        "| 20 / 20 / 20 | 12 / 12 / 12 | - |"
    )
